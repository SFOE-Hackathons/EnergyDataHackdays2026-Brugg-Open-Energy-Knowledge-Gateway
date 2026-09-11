#!/usr/bin/env python3
"""Render a knowledge-graph .ttl file to an interactive HTML visualization.

Usage:
    python visualize_graph.py [path/to/knowledge-graph.ttl] [-o output.html]

One node per :Document and one node per :Entity (kg-gen extraction output,
see kb_graph/entity_extraction.py) — both typed subjects in the graph. Short
literal properties (tags, category, content type, ...) become small shared
"value" nodes so documents/entities with matching metadata cluster together.
Any property whose object is itself a known node (:mentions from a Document
to an Entity, or a kg-gen relation between two Entities) becomes a direct
edge instead. Long/unique literals (:sourceUri) are shown in the hover
tooltip only, never as nodes — a value node per unique URI would just add
clutter. Document bodies are no longer in the graph at all.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import networkx as nx
import rdflib
from pyvis.network import Network

MAX_VALUE_NODE_CHARS = 80  # literals longer than this stay tooltip-only
LAYOUT_SEED = 42  # fixed so the same .ttl always lays out identically
LAYOUT_SPACING = 60  # px between nodes, before the sqrt(node count) scale-up
SKIP_LOCAL_NAMES = {
    "sourceUri",
    "sizeBytes",
    "hasText",
    "textTruncated",
    "title",  # already promoted to the document's own node label — would just duplicate it
    "label",  # already promoted to the entity's own node label — would just duplicate it
}
SKIP_EDGE_LOCAL_NAMES = {
    # :Label nodes are a lexical grouping, not a drawn node type here.
    "hasLabelForm",
}


def local_name(uri: str) -> str:
    return uri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def _first(props: list[tuple], name: str) -> str | None:
    """First value for a (possibly repeated) predicate's local name, or None."""
    return next((str(o) for p, o in props if local_name(str(p)) == name), None)


def friendly_label(doc_iri: str, props: list[tuple]) -> str:
    """A human-readable label — never the hash-based doc IRI local name."""
    title = _first(props, "title")
    if title and title.strip():
        return title.strip()[:40]

    source_uri = _first(props, "sourceUri")
    if source_uri:
        filename = source_uri.rstrip("/").rsplit("/", 1)[-1]
        if filename:
            return filename[:40]

    return local_name(doc_iri)[:12]  # last resort — genuinely nothing else to show


def load_graph(ttl_path: Path) -> rdflib.Graph:
    g = rdflib.Graph()
    g.parse(str(ttl_path), format="turtle")
    return g


def _tooltip(heading: str, props: list[tuple]) -> str:
    """Literal properties only — a URIRef target (:mentions, a kg-gen
    relation) is already drawn as an edge, so repeating it here would just
    flood the tooltip (a well-connected entity can have hundreds)."""
    lines = [f"<b>{heading}</b>"]
    for p, o in sorted(props, key=lambda po: local_name(str(po[0]))):
        if not isinstance(o, rdflib.Literal):
            continue
        name = local_name(str(p))
        lines.append(f"<i>{name}</i>: {o}")
    return "<br>".join(lines)


def build_network(g: rdflib.Graph) -> nx.Graph:
    nx_graph = nx.Graph()
    rdf_type = rdflib.RDF.type

    documents = {
        s for s, _, o in g.triples((None, rdf_type, None)) if local_name(str(o)) == "Document"
    }
    entities = {
        s for s, _, o in g.triples((None, rdf_type, None)) if local_name(str(o)) == "Entity"
    }
    node_ids = {str(s) for s in documents} | {str(s) for s in entities}

    for doc in documents:
        props = [(p, o) for _, p, o in g.triples((doc, None, None)) if p != rdf_type]
        content_type = _first(props, "contentType") or ""
        nx_graph.add_node(
            str(doc),
            label=friendly_label(str(doc), props),
            title=_tooltip(local_name(str(doc)), props),
            group=content_type or "unknown",
            shape="dot",
            size=16,
            font={"size": 16, "face": "arial"},
        )

    for entity in entities:
        props = [(p, o) for _, p, o in g.triples((entity, None, None)) if p != rdf_type]
        label = _first(props, "label") or local_name(str(entity))[:16]
        nx_graph.add_node(
            str(entity),
            label=label[:40],
            title=_tooltip(label, props),
            group="entity",
            shape="diamond",
            color="#6aa9e0",
            size=14,
            font={"size": 14, "face": "arial"},
        )

    # Second pass: every non-type triple becomes either a direct edge (when
    # its object is another node we already added above — :mentions from a
    # Document to an Entity, or a kg-gen relation between two Entities) or a
    # small shared "value" node (when its object is a short literal). A
    # subject can repeat the same predicate many times (every :mentions,
    # every kg-gen relation predicate reused across entities) — iterate every
    # triple, never collapse by predicate into a dict.
    for subject in documents | entities:
        props = [(p, o) for _, p, o in g.triples((subject, None, None)) if p != rdf_type]
        for p, o in props:
            name = local_name(str(p))
            if isinstance(o, rdflib.Literal):
                if name in SKIP_LOCAL_NAMES:
                    continue
                value = str(o)
                if not value.strip() or len(value) > MAX_VALUE_NODE_CHARS:
                    continue
                value_node_id = f"{name}={value}"
                if value_node_id not in nx_graph:
                    nx_graph.add_node(
                        value_node_id,
                        label=f"{name}: {value[:24]}",
                        title=f"{name}: {value}",
                        shape="box",
                        color="#e0c36a",
                        size=10,
                        font={"size": 12, "face": "arial"},
                    )
                nx_graph.add_edge(
                    str(subject), value_node_id, title=name, label=name, font={"size": 9, "color": "#999999"}
                )
            elif str(o) in node_ids:
                if name in SKIP_EDGE_LOCAL_NAMES:
                    continue
                edge_label = name.replace("_", " ")
                nx_graph.add_edge(
                    str(subject), str(o), title=name, label=edge_label, font={"size": 9, "color": "#999999"}
                )

    return nx_graph


def _assign_static_positions(nx_graph: nx.Graph) -> None:
    """Precompute one fixed (x, y) per node.

    vis.js only places nodes itself while the physics solver runs; with
    physics off every node without explicit coordinates lands on (0, 0). So
    the layout is done here instead, once, by networkx — a seeded
    spring_layout, deterministic across runs so the same .ttl always renders
    the same picture. Scale grows with node count to keep the spacing roughly
    constant as the graph gets bigger.
    """
    if not nx_graph:
        return
    scale = LAYOUT_SPACING * max(1.0, math.sqrt(nx_graph.number_of_nodes()))
    positions = nx.spring_layout(nx_graph, seed=LAYOUT_SEED, scale=scale)
    for node_id, (x, y) in positions.items():
        nx_graph.nodes[node_id].update(x=float(x), y=float(y), physics=False, fixed=True)


def render(nx_graph: nx.Graph, output_path: Path) -> None:
    _assign_static_positions(nx_graph)
    net = Network(height="900px", width="100%", notebook=False, cdn_resources="in_line")
    net.from_nx(nx_graph)
    # No physics and no physics config panel: the layout is fully determined
    # by the x/y set above. "fixed" plus dragNodes:false makes the drawing
    # immovable — panning and zooming still work.
    net.set_options("""
    {
      "physics": { "enabled": false },
      "nodes": {
        "fixed": { "x": true, "y": true },
        "font": { "size": 16, "face": "arial", "strokeWidth": 3, "strokeColor": "#ffffff" }
      },
      "edges": { "font": { "size": 9, "align": "middle" }, "color": { "color": "#cccccc" }, "smooth": false },
      "interaction": { "hover": true, "tooltipDelay": 100, "dragNodes": false }
    }
    """)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    net.write_html(str(output_path), open_browser=False, notebook=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "ttl_path", nargs="?", default="output/knowledge_graph.ttl", type=Path
    )
    parser.add_argument(
        "-o", "--output", default="output/knowledge_graph.html", type=Path
    )
    args = parser.parse_args()

    if not args.ttl_path.exists():
        print(
            f"{args.ttl_path} not found — run `kb-graph build` first.", file=sys.stderr
        )
        return 1

    g = load_graph(args.ttl_path)
    nx_graph = build_network(g)
    render(nx_graph, args.output)
    print(
        f"{nx_graph.number_of_nodes()} nodes, {nx_graph.number_of_edges()} edges -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
