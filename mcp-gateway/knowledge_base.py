"""Client for the Swiss federal energy publications knowledge base.

Implementation note (maintainers only, not exposed to callers): this calls the
Bedrock `Retrieve` API directly, with the ambient AWS identity. The raw,
deeply-nested Bedrock retrieval response is normalized here into a flat, simple
result shape before it ever reaches a tool.

WHY NOT THROUGH THE AGENTCORE GATEWAY. It used to be: this module minted a
Cognito token and called a `bfe-public-knowledge___Retrieve` tool on the
gateway, which forwarded to exactly this API. That hop was removed because the
gateway now sits in FRONT of this server rather than behind it -- clients reach
the gateway, the gateway reaches this server, and this server reaching back
into the same gateway would have made a loop with two AgentCore hops per search.

Three things fell out of removing it:

  - The Cognito client secret left the deployment entirely. Access is now an
    IAM grant (`bedrock:Retrieve` on the knowledge base) on whatever role this
    runs as, which also means an IAM-based budget action can actually throttle
    upstream spend -- it could not before.

  - `numberOfResults` became a request field again. The gateway's connector
    target pins it for every caller and rejects `parameterOverrides` outright
    (see infra/create-gateway.sh), which made retrieval breadth a property of
    the gateway rather than of the question. It is a plain field on this API.

  - `AgenticRetrieveStream` is no longer reachable. The connector exposed it
    alongside `Retrieve`; it plans and retrieves iteratively over several steps.
    Nothing here ever used it and the plain Retrieve API has no equivalent, so
    trying it would mean putting a connector target back.
"""

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from config import Config
from passages import dedupe
from public_source import PublicSourceResolver, parse_document_name
from semantics import (
    detect_projection,
    detect_truncation,
    extract_years_covered,
    infer_bases,
)

# How many passages to ask Bedrock for, regardless of how many the caller wants
# back. Deliberately NOT `max_results`.
#
# Two reasons it has to be an over-fetch. Passages are deduplicated below --
# the corpus stores the same publication under several document names, so a
# query routinely retrieves one passage twice -- and asking for exactly
# max_results would hand back fewer than requested once the copies collapse.
# And the managed reranker applies its own relevance cutoff, returning fewer
# than asked (50 -> ~37, 100 -> ~63), so the number is an upper bound rather
# than a promise.
#
# 50 preserves the value the gateway's connector target pinned, which was
# chosen with measurements: over 12 DE/FR/IT/EN questions, 5 results came from
# a mean of 3.4 distinct documents with 48% from a single one -- far too narrow
# a base for a national-scale question -- while 25 drew on 12.8 distinct
# documents with a 25% top-document share.
#
# This is now a per-request knob rather than a gateway-wide setting, so it
# COULD scale with max_results. It deliberately does not yet: retrieval quality
# and the transport were changed in the same step, and moving both at once
# would make a regression impossible to attribute. Hard limit is 100.
RETRIEVAL_BREADTH = 50


class KnowledgeBaseError(Exception):
    """A query against the knowledge base failed.

    Its message is surfaced to callers, so it must stay free of any
    implementation detail (see this module's docstring).
    """


class KnowledgeBaseClient:
    """Queries the Swiss federal energy publications knowledge base."""

    def __init__(
        self,
        config: Config,
        public_sources: PublicSourceResolver | None = None,
        client=None,
    ):
        self._config = config
        self._public_sources = public_sources or PublicSourceResolver()
        self._client = client or boto3.client(
            "bedrock-agent-runtime",
            region_name=config.region,
            # get_metric_timeline issues one retrieval per year, sequentially,
            # inside a single 60-second Lambda budget. A default 60-second read
            # timeout would let one slow call eat the whole budget and time the
            # request out with nothing to show; 15 seconds against a ~2s
            # measured call fails fast enough to leave room for the retry.
            config=BotoConfig(
                read_timeout=15,
                connect_timeout=5,
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    def search(self, query: str, max_results: int) -> list[dict]:
        """Run a semantic search and return a score-sorted, ranked list of
        normalized passages, truncated to max_results."""
        return self._normalize(self._retrieve(query), max_results)

    def _retrieve(self, query: str) -> list[dict]:
        try:
            response = self._client.retrieve(
                knowledgeBaseId=self._config.knowledge_base_id,
                retrievalQuery={"text": query},
                retrievalConfiguration={
                    # managedSearchConfiguration, not vectorSearchConfiguration:
                    # this is a managed knowledge base, and the vector variant
                    # is rejected for those with a ValidationException.
                    "managedSearchConfiguration": {
                        "numberOfResults": RETRIEVAL_BREADTH
                    }
                },
            )
        except (ClientError, BotoCoreError) as exc:
            # Deliberately not interpolated into the message. A ClientError
            # from Bedrock names the knowledge base id, the account and the
            # role that was denied, and this message goes to whoever called the
            # tool.
            raise KnowledgeBaseError("Knowledge base query failed.") from exc

        return response.get("retrievalResults", [])

    def _normalize(self, retrieval_results: list[dict], max_results: int) -> list[dict]:
        cleaned = []
        for item in retrieval_results:
            metadata = item.get("metadata", {})
            title = metadata.get("_document_title")
            published_at, _ = parse_document_name(title)
            text = item.get("content", {}).get("text", "")
            truncation = detect_truncation(text)
            cleaned.append(
                {
                    "score": item.get("score"),
                    "text": text,
                    "years_covered": extract_years_covered(text),
                    "bases": infer_bases(text),
                    "is_projection": detect_projection(text),
                    "is_truncated": truncation["is_truncated"],
                    "truncation_reasons": truncation["reasons"],
                    "source": {
                        "title": title,
                        "published_at": published_at,
                        "download_url": None,
                        "file_type": metadata.get("_file_type"),
                        "language": metadata.get("_language_code"),
                        "media_type": metadata.get("_media_type"),
                        "created_at": metadata.get("_created_at"),
                        "last_updated_at": metadata.get("_last_updated_at"),
                    },
                }
            )

        # Sorted with an explicit tie-break rather than on score alone.
        # Deduplication below makes the winner between two equally-scored
        # copies observable, so the order has to be a function of the data
        # and not of whatever sequence the upstream happened to return.
        cleaned.sort(
            key=lambda item: (
                -(item["score"] or 0),
                item["source"]["title"] or "",
            )
        )

        # Before the truncation, not after: the corpus stores the same
        # publication under more than one document name, so a query routinely
        # retrieves a passage twice. Deduplicating afterwards would hand back
        # fewer results than asked for; doing it here lets the next-best
        # distinct passage take the freed slot.
        cleaned = dedupe(cleaned)[:max_results]

        self._attach_download_urls(cleaned)

        # No `rank` field. The list order carries it for a single search, and
        # the tools that pool several searches (get_metric_timeline,
        # get_chart_data) would present a per-search rank in a flat combined
        # list, where it reads as a global ranking it is not.
        return cleaned

    def _attach_download_urls(self, results: list[dict]) -> None:
        """Fill in each source's public download URL, in place.

        Only the results actually being returned are resolved, and the
        resolver caches across calls, so a repeated document costs nothing.
        Documents that cannot be matched confidently keep a `None` URL.
        """
        titles = [item["source"]["title"] for item in results]
        resolved = self._public_sources.resolve_many(titles)
        for item in results:
            item["source"]["download_url"] = resolved.get(item["source"]["title"])
