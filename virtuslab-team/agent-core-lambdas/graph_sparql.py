"""graph_sparql — one tool, one Lambda, one file.

Read-only SPARQL over the energy knowledge graph, plus the schema an agent
needs in order to write a query that returns anything.

Input (the event is the parameter dict):

    query   str          a SELECT, ASK, CONSTRUCT or DESCRIBE query.
                         OMITTED -> the graph's schema comes back instead of
                         rows. That is the discovery call, not an error.
    limit   int = 50     cap on rows or triples returned. Hard maximum 500.

Output (200) — one shape, so a caller never has to branch on query form:

    {
      "columns": ["p", "n"],           SELECT only
      "rows": [{"p": "rel:part_of", "n": "106"}],
      "row_count": 1,
      "truncated": false,              true when `limit` cut the result short
      "boolean": null,                 ASK only
      "triples": null,                 CONSTRUCT / DESCRIBE only
      "hint": null                     set when the result is empty
    }

A refused or unparseable query comes back as 400 with {"error": "…"}, never as
an exception. Whatever is driving this tool has to be able to tell a person
what was wrong with the question and then try a different one; a stack trace is
not that.

WHY THE SCHEMA IS PART OF THE TOOL. This graph carries 621 distinct relation
predicates over 1876 facts — three facts per predicate — and they are not
normalised: `part_of` (106), `is_part_of` (71) and `are_part_of` (20) are three
separate predicates, as are `includes` (62) and `include` (30). An agent that
guesses a predicate's spelling misses almost every time, and a SPARQL endpoint
answers a near-miss with zero rows, which is indistinguishable from "the graph
does not know". So the schema is computed from the graph and returned on the
discovery call, and the most common predicates come back in `hint` on every
empty result. Computed, never hard-coded: written down, it would be a lie about
the next graph uploaded.

WHAT IS REFUSED, AND WHY IT IS REFUSED HERE RATHER THAN LEFT TO RDFLIB.

  Writes (INSERT, DELETE, LOAD, CLEAR, DROP, CREATE, ADD, MOVE, COPY, WITH).
  The graph is a read model whose only sanctioned change is a reviewed
  correction file. A SPARQL write would land in one Lambda's memory, vanish
  when the container is recycled, and bypass review entirely. rdflib's
  `Graph.query` would itself reject most of these — it routes updates through
  `update()` — but with a parser error that says nothing about why. The point
  of naming them is the message.

  SERVICE. rdflib implements it by opening an outbound HTTP connection to a
  host named in the query. That hands an SSRF primitive to whoever writes the
  question, which for a Lambda inside a VPC means the instance metadata
  endpoint and every internal address it can reach. This is the most important
  line in the file.

String literals are blanked out before the keyword scan. Without that, asking
about an entity labelled 'INSERT DATA' is refused as an attempted write — a
false positive that appears only on real content, which is the worst kind.

WHY THE PREFIXES ARE SPLIT PER PATH SEGMENT. A SPARQL prefixed name cannot
contain a slash, so `kg:relprop/is_produced_by` is a parse error rather than a
predicate — and every interesting IRI in this graph lives under such a segment.
Hence `rel:`, `ent:`, `doc:` and `lbl:` alongside `kg:`. Results are
abbreviated back to the same prefixes, so a value from one answer pastes
straight into the next query. Abbreviating to something that would not parse
would defeat the only reason to abbreviate at all.

The vocabulary namespace is read from the graph's own `kg:` prefix declaration
rather than configured, for the reason spelled out in graph_neighbors.py: a
configured value that drifted from the file would not raise, it would silently
return nothing.

Not covered: the corrections layer. This reads the generated Turtle only, so a
fact a reviewed correction retracted is still returned and a curated fact is
absent. The MCP gateway merges that layer at load; here it is a known gap.
"""
from __future__ import annotations

import collections
import json
import os
import re

import boto3
import rdflib
from rdflib import Literal, URIRef
from rdflib.namespace import RDF, RDFS

BUCKET = os.environ.get("GRAPH_BUCKET", "energy-knowledge-graph")
KEY = os.environ.get("GRAPH_KEY", "knowledge_graph.ttl")

# Only a fallback; the namespace is read out of the graph. See load_graph.
FALLBACK_BASE_URI = os.environ.get("KG_BASE_URI", "")

DEFAULT_LIMIT = 50
MAX_LIMIT = 500

# How many predicates and documents the schema and the hint list.
SCHEMA_PREDICATES = 20
SCHEMA_DOCUMENTS = 25
HINT_PREDICATES = 12

_WRITE_KEYWORDS = ("INSERT", "DELETE", "LOAD", "CLEAR", "DROP", "CREATE",
                   "ADD", "MOVE", "COPY", "WITH")
_NETWORK_KEYWORDS = ("SERVICE",)

# Every quoted form SPARQL allows, longest first so a triple-quoted string is
# not consumed as an empty single-quoted one.
_LITERAL = re.compile(r"'''.*?'''|\"\"\".*?\"\"\"|'[^'\n]*'|\"[^\"\n]*\"", re.S)

# prefix -> the path segment it stands for, under the vocabulary namespace.
_SEGMENTS = (("rel", "relprop/"), ("ent", "entity/"), ("doc", "doc/"),
             ("lbl", "label/"), ("asr", "assertion/"))

s3 = boto3.client("s3")

# Survives across warm invocations; empty on a cold start.
_CACHE: dict = {"etag": None, "graph": None, "base": None, "schema": None}

# namespace -> prefixes ordered for abbreviation. Keyed on the namespace, so a
# graph replaced by one under a different namespace gets its own entry rather
# than the previous graph's.
_PREFIX_ORDER: dict[str, list[tuple[str, str]]] = {}


class Refused(Exception):
    """The query was refused, or could not be parsed or run. Carried to the
    caller as text, never raised out of the handler."""


# --------------------------------------------------------------- the graph


def load_graph() -> tuple[rdflib.Graph, str]:
    """The cached parsed graph and its vocabulary namespace, re-read when the
    S3 object changes. head_object on every call is one cheap request and it is
    what makes a re-uploaded graph take effect without a redeploy."""
    etag = s3.head_object(Bucket=BUCKET, Key=KEY)["ETag"]
    if _CACHE["etag"] != etag or _CACHE["graph"] is None:
        body = s3.get_object(Bucket=BUCKET, Key=KEY)["Body"].read()
        graph = rdflib.Graph()
        graph.parse(data=body, format="turtle")
        declared = dict(graph.namespaces()).get("kg")
        base = str(declared) if declared else FALLBACK_BASE_URI
        if not base:
            raise Refused(
                f"s3://{BUCKET}/{KEY} declares no 'kg:' prefix and KG_BASE_URI "
                f"is unset, so the vocabulary namespace is unknown and every "
                f"query would silently return nothing")
        _CACHE["graph"] = graph
        _CACHE["base"] = base
        # Invalidated with the graph, so the schema can never describe a file
        # that is no longer being served.
        _CACHE["schema"] = None
        _CACHE["etag"] = etag
    return _CACHE["graph"], _CACHE["base"]


def prefixes(base: str) -> dict:
    """The prefix map bound for every query, and the one `_short` inverts."""
    bound = {"kg": rdflib.Namespace(base), "rdf": RDF, "rdfs": RDFS}
    for prefix, segment in _SEGMENTS:
        bound[prefix] = rdflib.Namespace(f"{base}{segment}")
    return bound


def _prefix_order(base: str) -> list[tuple[str, str]]:
    """Every bound prefix as (prefix, namespace text), longest namespace first.

    Longest first is what makes the segment prefixes win over the bare `kg:`
    they are nested inside: `<base>relprop/` is longer than `<base>`, so a
    predicate abbreviates to `rel:is_produced_by` rather than to the
    unparseable `kg:relprop/is_produced_by`. Computed once per namespace,
    since it is consulted for every term of every row.
    """
    cached = _PREFIX_ORDER.get(base)
    if cached is None:
        cached = sorted(((prefix, str(namespace))
                         for prefix, namespace in prefixes(base).items()),
                        key=lambda item: -len(item[1]))
        _PREFIX_ORDER[base] = cached
    return cached


def _short(term, base: str):
    """Abbreviate an IRI to a bound prefix, but only when the result parses.

    Abbreviating exists for one reason: a value from one answer has to paste
    straight into the next query. So an abbreviation that would not parse is
    worse than none, and a local part still containing "/" or "#" is exactly
    that — a SPARQL prefixed name cannot contain either. `kg:assertion/0085…`
    looks helpful and is a syntax error, so the full IRI is returned instead.

    rdf: and rdfs: are abbreviated for the same reason as the graph's own
    prefixes: they are bound for every query, so `rdf:type` in a result is a
    term the caller can reuse, while the full syntax-ns# IRI is one they would
    have to translate by hand.
    """
    if term is None:
        return None
    if isinstance(term, Literal):
        return str(term)
    if isinstance(term, URIRef):
        text = str(term)
        for prefix, namespace in _prefix_order(base):
            if text.startswith(namespace):
                local = text[len(namespace):]
                if "/" in local or "#" in local:
                    return text
                return f"{prefix}:{local}"
        return text
    return str(term)


# ----------------------------------------------------------- what is refused


def _strip_literals(query: str) -> str:
    return _LITERAL.sub(" ", query)


def _check(query: str) -> None:
    bare = _strip_literals(query)
    # `(?<![\w?$])` rather than `(?<!\w)`: `?add` is a variable and `?` is not
    # a word character, so a lookbehind on `\w` alone would refuse it.
    for keyword in _NETWORK_KEYWORDS:
        if re.search(rf"(?<![\w?$]){keyword}(?![\w])", bare, re.IGNORECASE):
            raise Refused(
                f"{keyword} is not allowed: rdflib implements it by opening an "
                f"outbound connection to a host named in the query")
    for keyword in _WRITE_KEYWORDS:
        if re.search(rf"(?<![\w?$]){keyword}(?![\w])", bare, re.IGNORECASE):
            raise Refused(
                f"this endpoint is read-only and {keyword} writes. The graph "
                f"changes only through a reviewed correction file, never "
                f"through a query")


# ------------------------------------------------------------- the schema


def _predicate_counts(graph: rdflib.Graph, base: str) -> tuple[dict, dict]:
    """(vocabulary predicates, relation predicates), each IRI -> count.

    Counted in Python rather than with a GROUP BY: it is one pass over the
    triples for data the caller did not ask for, and it runs on every
    discovery call.
    """
    relprop = f"{base}relprop/"
    vocabulary: collections.Counter = collections.Counter()
    relations: collections.Counter = collections.Counter()
    for predicate in graph.predicates():
        text = str(predicate)
        (relations if text.startswith(relprop) else vocabulary)[text] += 1
    return dict(vocabulary), dict(relations)


def schema(graph: rdflib.Graph, base: str) -> dict:
    """Everything a caller needs to write a query that returns rows.

    Cached alongside the graph, because it is a full pass over the triples and
    the answer only changes when the file does.
    """
    if _CACHE["schema"] is not None:
        return _CACHE["schema"]

    vocabulary, relations = _predicate_counts(graph, base)
    relprop = f"{base}relprop/"

    types = collections.Counter(
        _short(node_type, base) for node_type in graph.objects(None, RDF.type))

    documents = []
    for doc in sorted(graph.subjects(RDF.type, URIRef(f"{base}Document")),
                      key=str):
        uri = graph.value(doc, URIRef(f"{base}sourceUri"))
        documents.append({"doc": _short(doc, base),
                          "source_uri": str(uri) if uri else None})

    built = {
        "namespace": base,
        "prefixes": {prefix: str(namespace)
                     for prefix, namespace in prefixes(base).items()},
        "triples": len(graph),
        "node_types": [{"type": t, "count": n} for t, n in types.most_common()],
        "vocabulary_predicates": [
            {"predicate": _short(URIRef(p), base), "count": n}
            for p, n in sorted(vocabulary.items(), key=lambda kv: -kv[1])],
        "relation_predicates": {
            "distinct": len(relations),
            "facts": sum(relations.values()),
            "most_common": [
                {"predicate": f"rel:{p[len(relprop):]}", "count": n}
                for p, n in sorted(relations.items(),
                                   key=lambda kv: -kv[1])[:SCHEMA_PREDICATES]],
        },
        "documents": {
            "count": len(documents),
            "listed": documents[:SCHEMA_DOCUMENTS],
        },
        "shape": [
            "doc:<id>   a kg:Document ; kg:sourceUri \"s3://…\" ; "
            "kg:mentions -> ent:<id>",
            "ent:<id>   a kg:Entity ; kg:label \"Wasserkraft\" ; "
            "kg:hasLabelForm -> lbl:<id>",
            "lbl:<id>   a kg:Label ; kg:labelText \"wasserkraft\"",
            "ent:<id>   rel:<slug> ent:<id>          one extracted fact",
            "kg:assertion/<id> a kg:Assertion ; rdf:subject/predicate/object "
            "-> the same fact, plus kg:derivedFrom and kg:extractionModel",
        ],
        "read_this_first": [
            "A predicate's slug replaces every non-alphanumeric character of "
            "its spoken form with '_': 'is produced by' is rel:is_produced_by. "
            "Note rel:, not kg:relprop/ — a SPARQL prefixed name cannot "
            "contain '/'.",
            f"There are {len(relations)} distinct relation predicates over "
            f"{sum(relations.values())} facts and they are NOT normalised: "
            f"'part of', 'is part of' and 'are part of' are three different "
            f"predicates. Find the predicate before filtering on it; do not "
            f"guess its spelling.",
            "An entity node is scoped to one document, so the same name "
            "usually has several nodes. Match on kg:label, not on a node id, "
            "unless you mean one document's reading of it.",
            "Every fact is also written as a kg:Assertion. Matching on a "
            "variable predicate therefore returns each fact twice plus the "
            "assertion nodes themselves; filter on the predicate.",
            "Writes and SERVICE are refused. A refusal comes back as an "
            "error, not as an empty result.",
        ],
        "examples": [
            {"what": "which predicates does one entity actually carry",
             "query": 'SELECT DISTINCT ?p WHERE { ?s kg:label "Wasserkraft" . '
                      '?s ?p ?o }'},
            {"what": "every relation predicate, most used first",
             "query": "SELECT ?p (COUNT(*) AS ?n) WHERE { ?s ?p ?o "
                      'FILTER STRSTARTS(STR(?p), STR(rel:)) } '
                      "GROUP BY ?p ORDER BY DESC(?n)"},
            {"what": "what points at an entity, and with what",
             "query": 'SELECT ?slabel ?p WHERE { ?o kg:label "Wasserkraft" . '
                      "?s ?p ?o . ?s kg:label ?slabel "
                      "FILTER STRSTARTS(STR(?p), STR(rel:)) }"},
            {"what": "which documents mention an entity",
             "query": 'SELECT ?uri WHERE { ?e kg:label "Wasserkraft" . '
                      "?d kg:mentions ?e ; kg:sourceUri ?uri }"},
            {"what": "which model extracted a fact",
             "query": "SELECT ?s ?p ?o ?model WHERE { ?a a kg:Assertion ; "
                      "rdf:subject ?s ; rdf:predicate ?p ; rdf:object ?o ; "
                      "kg:extractionModel ?model }"},
            {"what": "find a label without knowing its exact spelling",
             "query": "SELECT DISTINCT ?label WHERE { ?e kg:label ?label "
                      'FILTER CONTAINS(LCASE(STR(?label)), "wasser") }'},
        ],
    }
    _CACHE["schema"] = built
    return built


def _hint(graph: rdflib.Graph, base: str) -> str:
    """What to say when nothing matched.

    An empty result and a misspelled predicate are indistinguishable, and with
    621 predicates over 1876 facts the misspelling is by far the likelier one.
    So an empty result carries the predicates that do exist.
    """
    _vocabulary, relations = _predicate_counts(graph, base)
    relprop = f"{base}relprop/"
    top = ", ".join(
        f"rel:{p[len(relprop):]} ({n})"
        for p, n in sorted(relations.items(),
                           key=lambda kv: -kv[1])[:HINT_PREDICATES])
    return (
        f"no row matched. This graph carries {len(relations)} distinct "
        f"relation predicates over {sum(relations.values())} facts and they "
        f"are not normalised, so find the predicate before filtering on it "
        f"rather than guessing its spelling. The most common are: {top}. "
        f"Call this tool with no query to get the full schema.")


# --------------------------------------------------------------- the query


def run(graph: rdflib.Graph, base: str, query: str, limit: int) -> dict:
    """Execute a read-only query. `limit` is applied while the result is being
    consumed, not afterwards, so a query without its own LIMIT cannot
    materialise an unbounded join."""
    _check(query)

    try:
        result = graph.query(query, initNs=prefixes(base))
    except Refused:
        raise
    except Exception as exc:
        raise Refused(f"the query could not be parsed or executed: "
                      f"{type(exc).__name__}: {exc}") from exc

    payload: dict = {"columns": [], "rows": [], "row_count": 0,
                     "truncated": False, "boolean": None, "triples": None,
                     "hint": None}

    if result.type == "ASK":
        payload["boolean"] = bool(result.askAnswer)
        return payload

    if result.type in ("CONSTRUCT", "DESCRIBE"):
        triples = []
        for subject, predicate, obj in result.graph:
            if len(triples) >= limit:
                payload["truncated"] = True
                break
            triples.append([_short(subject, base), _short(predicate, base),
                            _short(obj, base)])
        payload["triples"] = triples
        payload["row_count"] = len(triples)
        if not triples:
            payload["hint"] = _hint(graph, base)
        return payload

    columns = [str(var) for var in (result.vars or [])]
    rows = []
    for row in result:
        if len(rows) >= limit:
            payload["truncated"] = True
            break
        rows.append({column: _short(row[column], base) for column in columns})
    payload["columns"] = columns
    payload["rows"] = rows
    payload["row_count"] = len(rows)
    if not rows:
        payload["hint"] = _hint(graph, base)
    return payload


def lambda_handler(event, context):
    try:
        params = event if isinstance(event, dict) else {}

        raw_limit = params.get("limit", DEFAULT_LIMIT)
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            return _reply(400, {"error": f"limit must be an integer, got {raw_limit!r}"})
        limit = max(1, min(limit, MAX_LIMIT))

        query = params.get("query")
        graph, base = load_graph()

        # No query is the discovery call. Deliberately not an error: an agent
        # that cannot ask what the vocabulary is will guess it, and a guessed
        # predicate returns zero rows rather than a complaint.
        if query is None or (isinstance(query, str) and not query.strip()):
            return _reply(200, {"schema": schema(graph, base)})

        if not isinstance(query, str):
            return _reply(400, {"error": f"query must be a string, got "
                                         f"{type(query).__name__}"})

        return _reply(200, run(graph, base, query, limit))

    except Refused as exc:
        # A refusal or a bad query is the caller's to fix, so it is a 400 and it
        # carries the reason as text.
        return _reply(400, {"error": str(exc)})
    except Exception as exc:
        return _reply(500, {"error": f"{type(exc).__name__}: {exc}"})


def _reply(status: int, payload: dict) -> dict:
    # ensure_ascii=False because the corpus is German: escaping "abhängig"
    # makes every log line unreadable for no gain.
    return {"statusCode": status,
            "body": json.dumps(payload, ensure_ascii=False)}
