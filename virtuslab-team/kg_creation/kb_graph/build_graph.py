"""Expand the DataFrames from transform.py against the templates in
graph_templates.py and write the resulting graph to Turtle.

NOTE: maplib's public API was verified against
https://datatreehouse.github.io/maplib/maplib.html at the time this was
written (Model/add_template/map/write, Template/Parameter/Variable/Triple).
Re-check against whatever version ends up pinned in pyproject.toml before
relying on this in production — a minor API drift is the most likely
breakage point in this whole pipeline.
"""
from __future__ import annotations

from pathlib import Path

import polars as pl
from maplib import Model

from .graph_templates import build_templates


def build_graph(
    documents_df: pl.DataFrame,
    properties_df: pl.DataFrame,
    base_uri: str,
    output_path: Path,
    labels_df: pl.DataFrame | None = None,
    entities_df: pl.DataFrame | None = None,
    relations_df: pl.DataFrame | None = None,
) -> Model:
    templates = build_templates(base_uri)

    model = Model()
    model.add_prefixes({"kg": base_uri})
    for template in templates.values():
        model.add_template(template)

    # Pass the Template objects, not `.iri` — mapping by IRI string looked up
    # against add_template's internal registry did not resolve reliably in
    # testing (maplib 0.13); the Template object itself works.
    if documents_df.height:
        model.map(templates["document"], documents_df)
    if properties_df.height:
        model.map(templates["property"], properties_df)
    # Labels before entities: an entity row references its label node.
    if labels_df is not None and labels_df.height:
        model.map(templates["label"], labels_df)
    if entities_df is not None and entities_df.height:
        model.map(templates["entity"], entities_df)
    if relations_df is not None and relations_df.height:
        model.map(templates["relation"], relations_df)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    model.write(str(output_path), format="turtle", prefixes={"kg": base_uri})
    return model
