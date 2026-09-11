"""graph_neighbors — one tool, one Lambda, one file.

Finds the entity the caller names and returns the entities directly connected
to it, with the predicate and the direction of each connection.

Input (the event is the parameter dict):

    entity     str            required. The label as the caller types it.
                              Matched case-insensitively, whitespace trimmed.
    direction  str   = "both" "out" | "in" | "both". Which way a connection
                              may point to count.

Output (200):

    {
      "entity": "Wasserkraft",     the label as the graph spells it
      "found": true,
      "nodes": 14,                 how many graph nodes carry that label
      "direction": "both",
      "neighbor_count": 79,
      "neighbors": [
        {"label": "Schweiz",
         "via": [{"predicate": "abhängig von", "direction": "in"}]},
        ...
      ]
    }

An entity the graph does not carry is a result, not an error: the same shape
comes back with "found": false, "nodes": 0 and an empty list. A caller asking
about something absent has to be able to tell that apart from a failure.

Every count below was measured on the file this Lambda serves — 205510
triples, 16318 label triples over 12706 distinct labels — and not assumed. A
rebuilt corpus moves them, so read them as the shape of the graph rather than
as constants.

DIRECT NEIGHBOURS ONLY — by decision, and the decision is about meaning, not
cost. Walking further is cheap off the index: level by level from Wasserkraft
it measures 0.5ms, 10ms and 53ms for one, two and three hops. But it reaches
79, then 891, then 3005 labels — a quarter of every label in the graph — and
it does so because an entity is connected to every other entity mentioned in
the same document. Three hops is not a broader answer, it is the corpus with
the question thrown away. Two hops would be defensible; it is a different tool
with a different contract, and it is not this one.

Three properties of this graph drive everything below.

1. AN ENTITY IS SCOPED TO ONE DOCUMENT, so one name has several nodes —
   "Wasserkraft" has 14, one per document that mentions it. kg-builder does
   that deliberately, so two documents cannot silently merge two different
   things that share a name. A caller asking about "Wasserkraft", though,
   means the thing, not one document's reading of it. So the label resolves to
   every node carrying it, all of them are walked, and the results are grouped
   back by label — otherwise "Schweiz" comes back fourteen times.

2. THE DIRECTION AN ARROW POINTS IS AN ACCIDENT OF THE SENTENCE it was
   extracted from, not a convention. Measured: 35 of Wasserkraft's neighbours
   are reached by an outgoing arrow and 47 by an incoming one, so counting
   only outgoing ones loses 44 of the 79 — including "Schweiz abhängig von
   Wasserkraft", which is the answer to "what depends on hydropower". Hence
   `direction`, defaulting to "both", and hence every neighbour carrying the
   direction it was found by.

3. MOST EDGES AT A NODE ARE NOT FACTS. 248 edges touch Wasserkraft's 14
   nodes; 89 of them are connections to another named thing. The 159 dropped
   are 89 edges into the kg:Assertion blocks that record which model extracted
   what, and 14 each of rdf:type, kg:label, kg:hasLabelForm, kg:mentionCount
   and kg:mentions. So a neighbour needs two things and both filters are
   load-bearing: a predicate under relprop/, and an end that carries a label
   of its own — a relprop edge can also end on a literal, which is a value
   rather than an entity.

WHY THERE IS NO QUERY ENGINE HERE. This tool asks one fixed question, and it
is precisely the question rdflib's own triple indexes answer directly: the
edges of a known node. Going through SPARQL to ask it cost 12.79s for
Wasserkraft against 0.62ms off a label index built in 0.043s — and across 40
labels sampled from the graph, 93.06s against 4.8ms, with identical answers in
all 40 cases. The index is not a cache of the query results; it is the same
lookup the query engine would have had to perform, minus the planner that made
the wrong plan.

Two things the SPARQL version had to work around, recorded so nobody
reintroduces them. Resolving the label had to be its own query with the node
IRIs then handed to the neighbour query through VALUES: folded into one query
with the seed unbound, rdflib paired 16318 label triples against all 205510
triples and did not finish in two minutes, because it does not reorder joins.
And the neighbour query needed `?n != ?e` written out, which is what the
`other == node` line still does — it drops the graph's two self-loops without
dropping an edge between two distinct nodes that happen to share a label.

The 9.4 MB Turtle file takes 5.1s to parse, which is now the whole cost of a
cold start: the index over it takes 0.043s. So the parsed index is held in a
module global across warm invocations and rebuilt only when the object's ETag
moves. head_object on every call is one cheap request, and it is what makes a
re-uploaded graph take effect without a redeploy. Removing the parse too means
not shipping Turtle to this Lambda at all — kg-builder writing the index as
its own artefact — which is a change to the build, not to this file.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

import boto3
import rdflib
from rdflib import URIRef
from rdflib.term import Node

BUCKET = os.environ.get("GRAPH_BUCKET", "energy-knowledge-graph")
KEY = os.environ.get("GRAPH_KEY", "knowledge_graph.ttl")

# Only a fallback. The vocabulary's namespace is read out of the graph itself
# (see `load_index`); this is consulted only when the file fails to declare it.
FALLBACK_BASE_URI = os.environ.get("KG_BASE_URI", "")

DIRECTIONS = ("out", "in", "both")

s3 = boto3.client("s3")

# Survives across warm invocations; empty on a cold start.
_CACHE: dict = {"etag": None, "index": None}


def _normalise(text: str) -> str:
    return text.strip().lower()


class EntityIndex:
    """A label index over the parsed graph, and the two questions it answers.

    Built once per graph, in one pass over the label triples: 0.043s for
    the 16318 of them in the production file. Everything after that is a dict
    lookup and a walk over the edges of a handful of known nodes.
    """

    def __init__(self, graph: rdflib.Graph, base: str):
        self.graph = graph
        self.base = base
        self.relprop = f"{base}relprop/"

        # normalised label -> the nodes carrying it, and the spellings it was
        # written with. Both are needed: the nodes to walk from, the spellings
        # to answer with (`resolve` reports the graph's own spelling, and
        # several spellings can share one normalised form).
        self._nodes: dict[str, list[URIRef]] = defaultdict(list)
        self._spellings: dict[str, set[str]] = defaultdict(set)

        # node -> its labels. A list, not a single value: nothing in the
        # vocabulary stops a node from carrying two, and a neighbour reached
        # once has to answer under every label it has, the way the join in a
        # query would have produced one row per label.
        self._labels: dict[URIRef, list[str]] = defaultdict(list)

        for node, label in graph.subject_objects(URIRef(f"{base}label")):
            spelling = str(label)
            key = _normalise(spelling)
            self._nodes[key].append(node)
            self._spellings[key].add(spelling)
            self._labels[node].append(spelling)

    def resolve(self, entity: str) -> tuple[str | None, list[URIRef]]:
        """(the spelling the graph uses, every node carrying that label).

        The label resolves to every node, not to one: an entity is scoped to a
        document, so one name is several nodes, and a caller asking about
        "Wasserkraft" means the thing rather than one document's reading of it.
        """
        key = _normalise(entity)
        nodes = self._nodes.get(key)
        if not nodes:
            return None, []
        # Sorted so the answer does not depend on the order the file was
        # parsed in — the spelling for a normalised form that several
        # spellings share, and the node list for the count reported with it.
        return min(self._spellings[key]), sorted(nodes, key=str)

    def neighbours(self, nodes: list[URIRef], direction: str) -> list[dict]:
        """Direct neighbours of `nodes`, grouped by label.

        Grouped rather than returned edge by edge because one name is several
        nodes and two nodes can be joined by more than one predicate: for
        Wasserkraft, 89 edges collapse to 79 labels. Every predicate joining
        the pair is kept in `via` — picking one of them would be picking
        arbitrarily.
        """
        via: dict[str, set[tuple[str, str]]] = defaultdict(set)

        for node in nodes:
            if direction in ("out", "both"):
                for _, predicate, other in self.graph.triples((node, None, None)):
                    self._record(via, node, predicate, other, "out")
            if direction in ("in", "both"):
                for other, predicate, _ in self.graph.triples((None, None, node)):
                    self._record(via, node, predicate, other, "in")

        return [
            {"label": label,
             "via": [{"predicate": predicate, "direction": way}
                     for predicate, way in sorted(via[label])]}
            for label in sorted(via)
        ]

    def _record(self, via: dict, node: URIRef, predicate: Node,
                other: Node, way: str) -> None:
        """Keep the edge if it is a fact about a named thing.

        Two filters, and neither is optional. Only relprop/* is a fact — a node
        also carries kg:label, kg:hasLabelForm and kg:mentionCount, sits under
        kg:mentions and kg:derivedFrom edges to its documents, and appears as
        the subject of kg:Assertion blocks recording which model extracted
        what. And only a labelled node is a neighbour: a relprop edge can end
        on a literal, which is a value rather than an entity.
        """
        if not str(predicate).startswith(self.relprop):
            return
        if other == node:
            return
        spoken = self._spoken(str(predicate))
        for label in self._labels.get(other, ()):
            via[label].add((spoken, way))

    def _spoken(self, predicate_iri: str) -> str:
        """relprop/is_produced_by -> "is produced by".

        Lossy on purpose, and visibly so: the builder slugged every
        non-alphanumeric character, so " is part of " is stored as
        `_is_part_of_` and comes back stripped to "is part of". This is a
        reading tool — nothing here has to round-trip back into a query.
        """
        return predicate_iri[len(self.relprop):].replace("_", " ").strip()


def load_index() -> EntityIndex:
    """The cached index, rebuilt when the S3 object changes.

    The vocabulary namespace is taken from the graph's own `kg:` prefix
    declaration rather than from configuration. Every IRI in the file is built
    from it — `kg:label` is really `<namespace>label` — so a configured value
    that disagreed with the file would not raise: it would match nothing, and
    an empty answer reads as an empty graph rather than as misconfiguration.
    Reading it from the file removes that failure mode. KG_BASE_URI remains as
    a fallback for a graph written with full IRIs and no prefix declaration,
    and its absence is an error rather than a guess.
    """
    etag = s3.head_object(Bucket=BUCKET, Key=KEY)["ETag"]
    if _CACHE["etag"] != etag or _CACHE["index"] is None:
        body = s3.get_object(Bucket=BUCKET, Key=KEY)["Body"].read()
        graph = rdflib.Graph()
        graph.parse(data=body, format="turtle")
        declared = dict(graph.namespaces()).get("kg")
        base = str(declared) if declared else FALLBACK_BASE_URI
        if not base:
            raise RuntimeError(
                f"s3://{BUCKET}/{KEY} declares no 'kg:' prefix and KG_BASE_URI "
                f"is unset, so the vocabulary namespace is unknown and every "
                f"lookup would silently return nothing")
        _CACHE["index"] = EntityIndex(graph, base)
        _CACHE["etag"] = etag
    return _CACHE["index"]


def lambda_handler(event, context):
    try:
        params = event if isinstance(event, dict) else {}

        entity = params.get("entity")
        if not isinstance(entity, str) or not entity.strip():
            return _reply(400, {"error": "entity is required and must be a non-empty string"})

        direction = params.get("direction", "both")
        if direction not in DIRECTIONS:
            return _reply(400, {
                "error": f"direction must be one of {', '.join(DIRECTIONS)}, "
                         f"got {direction!r}"})

        index = load_index()
        matched, nodes = index.resolve(entity)
        found = index.neighbours(nodes, direction) if nodes else []

        return _reply(200, {
            "entity": matched if matched is not None else entity,
            "found": matched is not None,
            "nodes": len(nodes),
            "direction": direction,
            "neighbor_count": len(found),
            "neighbors": found,
        })

    except Exception as exc:
        # The reason, not a traceback: whatever is on the other end of this
        # Lambda has to be able to tell a person what went wrong.
        return _reply(500, {"error": f"{type(exc).__name__}: {exc}"})


def _reply(status: int, payload: dict) -> dict:
    # ensure_ascii=False because the corpus is German: escaping "abhängig" into
    # abhängig makes every log line unreadable for no gain.
    return {"statusCode": status,
            "body": json.dumps(payload, ensure_ascii=False)}
 