"""Bedrock KB discovery: type, and the S3 bucket/prefix backing its data source(s).

Two data-source shapes are handled — confirmed against a real KB while
building this tool, not assumed from documentation alone:

- Classic/custom KB: `dataSourceConfiguration.type == "S3"`, with a nested
  `s3Configuration.bucketArn` (+ optional `inclusionPrefixes`).
- Managed KB: `dataSourceConfiguration.type == "MANAGED_KNOWLEDGE_BASE_CONNECTOR"`,
  with `managedKnowledgeBaseConnectorConfiguration.connectorParameters` — a
  **JSON-encoded string** (not a nested object) whose parsed `type` names
  the actual connector (S3, SharePoint, Confluence, ...). For an S3
  connector it carries `connectionConfiguration.bucketName` and
  `bucketOwnerAccountId` — the bucket can belong to a *different* AWS
  account than the one running the KB, which matters for IAM: a
  same-account role policy alone won't grant access, the bucket owner's
  account also needs a bucket policy permitting it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class S3DataSource:
    data_source_id: str
    bucket: str
    prefix: str
    bucket_owner_account_id: str | None = None


class UnsupportedDataSource(RuntimeError):
    """Raised when a data source is not S3-backed — this tool only handles S3."""


def get_knowledge_base(bedrock_agent, kb_id: str) -> dict:
    return bedrock_agent.get_knowledge_base(knowledgeBaseId=kb_id)["knowledgeBase"]


def list_data_sources(bedrock_agent, kb_id: str) -> list[dict]:
    sources: list[dict] = []
    next_token = None
    while True:
        kwargs = {"knowledgeBaseId": kb_id}
        if next_token:
            kwargs["nextToken"] = next_token
        resp = bedrock_agent.list_data_sources(**kwargs)
        sources.extend(resp.get("dataSourceSummaries", []))
        next_token = resp.get("nextToken")
        if not next_token:
            return sources


def get_data_source(bedrock_agent, kb_id: str, data_source_id: str) -> dict:
    return bedrock_agent.get_data_source(
        knowledgeBaseId=kb_id, dataSourceId=data_source_id
    )["dataSource"]


def _from_classic_s3(ds: dict, config: dict) -> S3DataSource | None:
    s3_config = config.get("s3Configuration")
    if not s3_config:
        return None
    bucket = s3_config["bucketArn"].rsplit(":", 1)[-1]  # bucketArn -> bucket name
    prefixes = s3_config.get("inclusionPrefixes") or [""]
    return S3DataSource(data_source_id=ds["dataSourceId"], bucket=bucket, prefix=prefixes[0])


def _from_managed_connector(ds: dict, config: dict) -> S3DataSource | None:
    managed_config = config.get("managedKnowledgeBaseConnectorConfiguration")
    if not managed_config:
        return None
    raw_params = managed_config.get("connectorParameters")
    if not raw_params:
        return None
    params = json.loads(raw_params) if isinstance(raw_params, str) else raw_params
    if params.get("type") != "S3":
        return None  # e.g. SharePoint/Confluence/Google Drive/OneDrive/Web Crawler — not handled here
    conn = params["connectionConfiguration"]
    return S3DataSource(
        data_source_id=ds["dataSourceId"],
        bucket=conn["bucketName"],
        prefix="",
        bucket_owner_account_id=conn.get("bucketOwnerAccountId"),
    )


def resolve_s3_data_source(bedrock_agent, kb_id: str, data_source_id: str | None) -> S3DataSource:
    """Find (or use the given) data source and confirm it's S3-backed.

    Raises UnsupportedDataSource if no candidate resolves to an S3 bucket —
    this tool's ingestion paths (text + PDF) assume direct S3 access.
    """
    if data_source_id:
        candidates = [get_data_source(bedrock_agent, kb_id, data_source_id)]
    else:
        summaries = list_data_sources(bedrock_agent, kb_id)
        if not summaries:
            raise UnsupportedDataSource(f"Knowledge base {kb_id!r} has no data sources.")
        candidates = [
            get_data_source(bedrock_agent, kb_id, s["dataSourceId"]) for s in summaries
        ]

    for ds in candidates:
        config = ds.get("dataSourceConfiguration", {})
        resolved = _from_classic_s3(ds, config) or _from_managed_connector(ds, config)
        if resolved:
            return resolved

    types = [ds.get("dataSourceConfiguration", {}).get("type") for ds in candidates]
    raise UnsupportedDataSource(
        f"No S3-backed data source found for knowledge base {kb_id!r} "
        f"(found types: {types}). This tool requires direct S3 access to the source corpus."
    )
