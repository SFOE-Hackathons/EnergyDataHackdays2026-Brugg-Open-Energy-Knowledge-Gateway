"""Shaping helpers for lists of normalized passages.

Pure functions over the flat passage dicts that `KnowledgeBaseClient`
produces. They exist as their own module, rather than as methods on the
client or the tools, because both layers need them: the client deduplicates
within a single retrieval, and `get_metric_timeline` deduplicates again
across the separate per-year retrievals it stitches together.
"""

import json

INDEX = "index"
FULL = "full"

CORE = "core"
ALL = "all"

# Kept at source_fields=CORE, in the order a reader wants them. Measured over
# 666 source records spanning 160 documents:
#
#   file_type        1 distinct value across every record ("PDF")
#   language         1 distinct value across every record ("en") -- and wrong:
#                    the corpus is largely German. It is the KB's own broken
#                    field.
#   created_at       byte-identical to last_updated_at on every single record
#   last_updated_at  70 distinct values, all of them inside the same minute
#                    (2026-08-31T13:07:xx). It is not "when the document was
#                    updated" -- it is when our knowledge base ingested it, and
#                    the whole corpus was ingested in one batch. published_at
#                    is the field that carries real document time.
#
# Those four cost ~72 KB per wide timeline and say nothing about the documents,
# so CORE omits them. They are still available at source_fields=ALL, because
# "this field is constant" is a fact about today's corpus, not a guarantee.
CORE_SOURCE_FIELDS = (
    "title",
    "published_at",
    "download_url",
    "media_type",
)

# Never dropped, at any setting, even when null. The download URL is the whole
# point of the attribution: it is what makes a cited figure checkable by
# someone who does not have access to this server. A null means the document
# could not be matched to a public record -- which the caller must be able to
# tell apart from the field simply having been trimmed away.
ALWAYS_SOURCE_FIELDS = ("download_url",)


def passage_key(passage: dict, position: int) -> str:
    """The identity of a passage, for deduplication.

    Whitespace is normalized because the upstream chunker is not consistent
    about line breaks inside an otherwise identical passage, and a raw string
    comparison would treat those as two different results.

    Keyed on text alone, deliberately NOT on (document, text): the corpus
    holds the same publication under more than one document name -- 293 of
    2430 stored objects are byte-identical duplicates, and the same report
    appears under two publication dates -- so including the document in the
    key would let precisely the duplicates we care about slip through.

    A passage with no text gets a key unique to its position, so empty
    results never collapse into one another. Merging unrelated empty results
    would be wrong, and dropping them would hide a retrieval problem instead
    of surfacing it.
    """
    text = " ".join(passage.get("text", "").split())
    return text or f"\x00empty:{position}"


def dedupe(passages: list[dict]) -> list[dict]:
    """Collapse passages with identical text, preserving first-seen order.

    The highest-scoring copy wins, so the surviving entry carries the score
    and source attribution of the document that matched best. It keeps the
    list position of the *first* copy, which leaves the caller's ordering
    (by score for a search, by year for a timeline) intact.

    When passages carry `queried_year` -- only timelines do -- the years of
    every copy are merged into a sorted `queried_years` list and the singular
    field is removed. A passage retrieved for both 2015 and 2016 is one
    passage that answered two queries, and collapsing it to a single year
    would throw away the more interesting half of that fact.
    """
    kept: dict[str, dict] = {}
    order: list[str] = []

    for position, passage in enumerate(passages):
        key = passage_key(passage, position)
        existing = kept.get(key)

        if existing is None:
            kept[key] = dict(passage)
            order.append(key)
        elif (passage.get("score") or 0) > (existing.get("score") or 0):
            # Take the better-scoring copy's fields, but carry over the years
            # already accumulated against this key.
            better = dict(passage)
            better["_years"] = existing.get("_years", [])
            kept[key] = better

        entry = kept[key]
        if "queried_year" in passage:
            years = entry.setdefault("_years", [])
            if passage["queried_year"] not in years:
                years.append(passage["queried_year"])

    result = []
    for key in order:
        entry = kept[key]
        years = entry.pop("_years", None)
        if years is not None:
            entry.pop("queried_year", None)
            entry["queried_years"] = sorted(years)
        result.append(entry)
    return result


def project_source(source: dict, source_fields: str) -> dict:
    """Trim a source record to the fields worth carrying.

    At CORE, the constant and redundant fields go (see CORE_SOURCE_FIELDS),
    and a field that is null for this document is omitted rather than sent as
    an explicit null -- `media_type` is null on roughly four records in five,
    and an absent key says the same thing for fewer bytes.

    The exception is ALWAYS_SOURCE_FIELDS, which are emitted even when null,
    because for those a null is itself the information.
    """
    if source_fields == ALL:
        return source

    trimmed = {}
    for field in CORE_SOURCE_FIELDS:
        if field not in source:
            continue
        value = source[field]
        if value is None and field not in ALWAYS_SOURCE_FIELDS:
            continue
        trimmed[field] = value

    for field in ALWAYS_SOURCE_FIELDS:
        trimmed.setdefault(field, source.get(field))
    return trimmed


def project(passages: list[dict], detail: str = FULL, source_fields: str = CORE) -> list[dict]:
    """Shape passages for the wire: verbatim text and source fields opt-in.

    At INDEX the text is 57% of a timeline response and the annotations that
    describe it are not, so an agent can decide what is worth reading for a
    fraction of the context. Everything else -- source, years_covered, bases,
    the projection and truncation flags -- is kept, because those are what
    make the index worth having.

    `detail` defaults to FULL here while the tools default it to INDEX: a
    tool that never wants to drop text should not have to name a value, and
    only the timeline exposes the choice.
    """
    shaped = []
    for passage in passages:
        item = {
            key: value
            for key, value in passage.items()
            if not (key == "text" and detail == INDEX)
        }
        if "source" in item:
            item["source"] = project_source(item["source"], source_fields)
        shaped.append(item)
    return shaped


def intern_sources(items: list[dict]) -> tuple[list[dict], dict]:
    """Hoist repeated source records into a shared lookup table.

    Returns `(items, sources)`, where each item has its inline `source`
    replaced by a `source_id` naming an entry in `sources`.

    A timeline is a list of passages drawn from a much smaller list of
    documents -- 307 entries across 72 distinct documents, measured -- so the
    same title and download URL are otherwise repeated verbatim four or five
    times each. Interning them is the single largest remaining saving in the
    response, and unlike dropping a field it loses nothing: every item still
    resolves to its full attribution in one lookup.

    Two sources are the same entry only when their records are equal after
    projection. That is deliberately strict: the corpus stores some
    publications under more than one document name, and those are genuinely
    different attributions even when they point at the same PDF. Collapsing
    them would mean reporting a title the passage was not filed under.

    Ids are assigned in order of first appearance, which makes them a
    function of the (already deterministically ordered) results rather than
    of dict iteration order.
    """
    sources: dict[str, dict] = {}
    ids_by_record: dict[str, str] = {}
    shaped = []

    for item in items:
        entry = dict(item)
        source = entry.pop("source", None)
        if source is not None:
            record = json.dumps(source, sort_keys=True, default=str)
            source_id = ids_by_record.get(record)
            if source_id is None:
                source_id = f"s{len(ids_by_record) + 1}"
                ids_by_record[record] = source_id
                sources[source_id] = source
            entry["source_id"] = source_id
        shaped.append(entry)

    return shaped, sources
