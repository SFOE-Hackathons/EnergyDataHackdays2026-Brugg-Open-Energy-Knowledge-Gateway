from __future__ import annotations

import logging
from pathlib import Path

import typer

from . import aws_clients, checkpoint, discover, ingest_pdf_full, ingest_text
from .config import MissingConfig, Settings
from .documents import DocumentRecord
from .inventory import human_bytes, list_objects, summarize_by_extension
from .ingest_text import TEXT_EXTENSIONS
from .manifest import load_manifest, pending_objects, save_manifest
from .transform import (
    documents_to_dataframe,
    entities_to_dataframe,
    labels_to_dataframe,
    properties_to_dataframe,
    relations_to_dataframe,
)

app = typer.Typer(
    help="Ingest a Bedrock Knowledge Base's S3 data source into an RDF graph."
)

# Full S3 keys (ObjectInfo.key, not source_uri) to ingest in `full`. Empty set
# means no filtering — every discovered object is processed. Fill in to
# restrict a run to a specific document list.
ALLOW_KEYS: set[str] = {
    "2020-06-01_schweizerische-statistik-der-erneuerbaren-energien-ausgabe-2019-vorabzug.pdf",
    "2024-08-01_2000-watt-areal.pdf",
    "2022-06-01_…erneuerbaren-energien-ausgabe-2021-vorabzug.pdf",
    "2020-05-01_kleinwasserkraft-gesamtdokumentation-modul-i.pdf",
    "2024-03-01_etude-de-vente-de-moteurs-electriques-pour-le-marche-suisse-en-2021.pdf",
    "2024-07-01_technology-monitoring-of-nuclear-energy.pdf",
    "2020-04-17_gesamte-erzeugung-und-abgabe-elektrischer-energie-in-der-schweiz-2019.pdf2022-03-10_die-neue-rolle-der-wasserkraft.pdf2020-01-31_modellierung-der-erzeugungs-und-systemkapazitat-(system-adequacy)-in-der-schweiz-im-bereich-strom-20.pdf2025-12-15_energiestrategie-2050-monitoring-bericht-2025-langfassung.pdf2021-08-04_fakten-zur-energie-nr-5-energiestrategie-2050.pdf2022-04-01_externe-evaluation-der-einmalvergutungen-fur-photovoltaik-anlagen-und-der-zusammenschlusse-zum-eigen.pdf2020-10-01_leitkonzept-fur-die-2000-watt-gesellschaft.pdf2025-07-10_statistik-sonnenenergie.pdf2023-09-01_schweizerische-statistik-der-erneuerbaren-energien.pdf2022-07-14_statistik-sonnenenergie.pdf2024-12-13_wasserstoffstrategie-fur-die-schweiz.pdf2022-09-27_thesen-zur-kunftigen-bedeutung-von-wasserstoff-in-der-schweizer-energieversorgung.pdf2023-11-15_wasserstoff-auslegeordnung-und-handlungsoptionen-fur-die-schweiz.pdf2020-10-28_kantonsseiten-energiejournal-2020.pdf",
    "2021-09-01_absicherungen-von-langfristigen-risiken-von-investitionen-in-die-klimavertragliche-modernisierung-vo.pdf",
    "2022-10-20_energieperspektiven-2050+.pdf",
    "2022-10-17_photovoltaikmarkt-preisbeobachtungsstudie-2021.pdf",
    "2024-12-13_wasserstoffstrategie-fur-die-schweiz.pdf",
    "2024-04-25_where-do-we-want-to-go-(and-how-do-we-get-there).pdf",
    "2020-04-17_gesamte-erzeugung-und-abgabe-elektrischer-energie-in-der-schweiz-2019.pdf",
    "2023-07-13_statistik-sonnenenergie.pdf",
    "2026-07-01_stand-der-energie-und-klimapolitik-in-den-kantonen-2026.pdf",
    "2025-09-01_schweizerische-statistik-der-erneuerbaren-energien.pdf",
    "2021-12-01_energiestrategie-2050-monitoring-bericht-2021-kurzfassung.pdf",
    "2020-10-28_kantonsseiten-energiejournal-2020.pdf",
    "2023-09-01_schweizerische-statistik-der-erneuerbaren-energien.pdf",
    "2021-08-04_fakten-zur-energie-nr-5-energiestrategie-2050.pdf2022-04-01_externe-evaluation-der-einmalvergutungen-fur-photovoltaik-anlagen-und-der-zusammenschlusse-zum-eigen.pdf",
    "2020-11-26_energiestrategie-2050-monitoring-bericht-2020-kurzfassung.pdf",
    "2022-11-01_etude-de-la-complementarite-solaire-eolien-en-suisse…",
    "2020-01-31_modellierung-der-erzeugungs-und-systemkapazitat-(system-adequacy)-in-der-schweiz-im-bereich-strom-20.pdf",
    "2021-09-01_absicherungen-von-langfristigen-risiken-von-investitionen-in-die-klimavertragliche-modernisierung-von-gebauden.pdf",
    "2023-03-27_power-system-flexibility-in-the-penta-region-current-state-and-challenges-for-a-future-decarbonised-energy-system.pdf",
    "2021-04-26_energieforschung-und-innovation-bericht-2020.pdf",
    "2026-06-26_…erneuerbaren-energien-2025-vorabzug.pdf",
    "2020-06-01_…erneuerbaren-energien-ausgabe-2019-vorabzug.pdf",
    "2020-06-19_schweizerische-elektrizitatsstatistik-2019.pdf",
    "2024-12-01_energiestrategie-2050-monitoring-bericht-2024-langfassung.pdf",
    "2023-12-04_energiestrategie-2050-monitoring-bericht-2023-kurzfassung.pdf",
    "2022-10-10_gemeinsame-position-blauer-wasserstoff-2022.pdf",
    "2020-10-01_kurzfassung-leitkonzept-fur-die-2000-watt-gesellschaft.pdf",
    "2021-08-04_fakten-zur-energie-nr-4-energieverbrauch-weltweit-und-in-der-schweiz.pdf",
    "2022-04-01_externe-evaluation-der-einmalvergutungen-fur-photovoltaik-anlagen-und-der-zusammenschlusse-zum-eigengebrauch-(zev)-2018-bis-2020.pdf",
    "2022-09-27_thesen-zur-kunftigen-bedeutung-von-wasserstoff-in-der-schweizer-energieversorgung.pdf",
    "2023-11-15_wasserstoff-auslegeordnung-und-handlungsoptionen-fur-die-schweiz.pdf",
    "2025-07-10_statistik-sonnenenergie.pdf",
    "2023-03-06_energiestrategie-2050-funfjahrliche-berichterstattung-im-rahmen-des-monitorings.pdf",
    "2022-03-10_die-neue-rolle-der-wasserkraft.pdf",
    "2022-07-14_statistik-sonnenenergie.pdf",
    "2022-06-01_schweizerische-statistik-der-erneuerbaren-energien-ausgabe-2021-vorabzug.pdf",
    "2023-06-15_leitfaden-zum-melde-und-bewilligungsverfahren-fur-solaranlagen.pdf",
    "2025-12-15_energiestrategie-2050-monitoring-bericht-2025-langfassung.pdf",
    "2021-12-01_energiestrategie-2050-monitoring-bericht-2021-langfassung.pdf",
    "2020-10-01_leitkonzept-fur-die-2000-watt-gesellschaft.pdf",
    "2025-07-02_absicherung-von-investitionen-zur-integration-der-schweiz-in-das-europaische-wasserstofftransportnet.pdf",
    "2023-09-01_futur-de-lhydrogene-en-suisse.pdf",
    "2020-06-12_windenergiestrategie-winterstrom-&-klimaschutz.pdf",
    "2025-07-02_absicherung-von-investitionen-zur-integration-der-schweiz-in-das-europaische-wasserstofftransportnetz.pdf",
    "2021-08-04_fakten-zur-energie-nr-5-energiestrategie-2050.pdf",
    "2023-07-07_ein-forderrahmen-fur-grunen-wasserstoff-in-der-schweiz.pdf",
}
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def _settings() -> Settings:
    try:
        return Settings.from_env()
    except MissingConfig as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1)


@app.command("discover")
def discover_cmd() -> None:
    """Print KB type and the resolved S3 data source — read-only, no download."""
    settings = _settings()
    session = aws_clients.build_session(settings)
    bedrock_agent = aws_clients.bedrock_agent_client(session)

    kb = discover.get_knowledge_base(bedrock_agent, settings.kb_id)
    typer.echo(f"Knowledge base: {kb['knowledgeBaseId']} ({kb['name']})")
    typer.echo(f"Type: {kb['knowledgeBaseConfiguration']['type']}")

    ds = discover.resolve_s3_data_source(
        bedrock_agent, settings.kb_id, settings.kb_data_source_id
    )
    typer.echo(f"Data source: {ds.data_source_id}")
    typer.echo(f"S3 bucket: {ds.bucket}  prefix: {ds.prefix!r}")

    if ds.bucket_owner_account_id:
        try:
            caller_account = session.client("sts").get_caller_identity()["Account"]
        except Exception:
            caller_account = None
        if caller_account and caller_account != ds.bucket_owner_account_id:
            typer.secho(
                f"Bucket is owned by account {ds.bucket_owner_account_id}, "
                f"but you're calling as account {caller_account} — this is a "
                "cross-account bucket. A same-account IAM policy on your role "
                "is not enough; the bucket owner's account must also grant "
                "access via a bucket policy (or you'll get AccessDenied on "
                "list/get even with s3:ListBucket/s3:GetObject in your own policy).",
                fg=typer.colors.YELLOW,
            )


@app.command("inventory")
def inventory_cmd() -> None:
    """Cheap full listing of the KB's S3 data source — no bodies downloaded."""
    settings = _settings()
    session = aws_clients.build_session(settings)
    bedrock_agent = aws_clients.bedrock_agent_client(session)
    s3 = aws_clients.s3_client(session)

    ds = discover.resolve_s3_data_source(
        bedrock_agent, settings.kb_id, settings.kb_data_source_id
    )
    objects = list_objects(s3, ds.bucket, ds.prefix)
    summary = summarize_by_extension(objects)

    typer.echo(f"{len(objects)} objects under s3://{ds.bucket}/{ds.prefix}\n")
    for ext, stats in sorted(summary.items(), key=lambda kv: -kv[1]["bytes"]):
        typer.echo(
            f"  .{ext:<10} {stats['count']:>6} files  {human_bytes(stats['bytes']):>10}"
        )
    total_bytes = sum(s["bytes"] for s in summary.values())
    typer.echo(f"\nTotal: {human_bytes(total_bytes)}")


@app.command()
def build(
    limit: int | None = typer.Option(
        None, help="Process at most this many PDFs (dry-run sizing)."
    ),
    prefix: str | None = typer.Option(
        None,
        help="Restrict to this S3 key prefix, on top of the data source's own prefix.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Skip the pre-flight size confirmation for the full PDF pull.",
    ),
    all_files: bool = typer.Option(
        False,
        "--all",
        help="Ignore ALLOW_KEYS and ingest every discovered object.",
    ),
) -> None:
    """Full pipeline: discover -> inventory -> ingest (text + PDF, full mode) -> build graph."""
    settings = _settings()
    session = aws_clients.build_session(settings)
    bedrock_agent = aws_clients.bedrock_agent_client(session)
    s3 = aws_clients.s3_client(session)

    ds = discover.resolve_s3_data_source(
        bedrock_agent, settings.kb_id, settings.kb_data_source_id
    )
    effective_prefix = f"{ds.prefix}{prefix or ''}"
    objects = list_objects(s3, ds.bucket, effective_prefix)
    allowed_uris: set[str] | None = None
    if ALLOW_KEYS and not all_files:
        objects = [o for o in objects if o.key in ALLOW_KEYS]
        allowed_uris = {f"s3://{ds.bucket}/{key}" for key in ALLOW_KEYS}

    text_objects = [o for o in objects if o.extension in TEXT_EXTENSIONS]
    pdf_objects = [o for o in objects if o.extension == "pdf"]
    if limit is not None:
        pdf_objects = pdf_objects[:limit]

    total_pdf_bytes = sum(o.size for o in pdf_objects)
    typer.echo(
        f"Inventory: {len(text_objects)} text files, {len(pdf_objects)} PDFs "
        f"({human_bytes(total_pdf_bytes)})."
    )

    if pdf_objects and limit is None and not yes:
        proceed = typer.confirm(
            f"About to download and extract {len(pdf_objects)} PDFs "
            f"(~{human_bytes(total_pdf_bytes)}). Continue?"
        )
        if not proceed:
            raise typer.Abort()

    paths = checkpoint.Paths.under(settings.checkpoint_dir)
    done_docs = checkpoint.load_documents(paths)
    if done_docs:
        typer.echo(
            f"Checkpoint at {settings.checkpoint_dir} holds {len(done_docs)} documents."
        )

    # Every ingested document is appended to the checkpoint the moment it is
    # extracted, so an interrupted run resumes from the last completed
    # document instead of re-downloading and re-extracting from scratch.
    for obj in checkpoint.pending_objects(text_objects, ds.bucket, done_docs):
        doc = ingest_text.ingest_text_object(s3, ds.bucket, obj)
        checkpoint.append_document(paths, doc)
        done_docs[doc.id] = doc

    # Held until after the graph is written — see the save_manifest call at the
    # end of this function for why.
    manifest_path = settings.pdf_manifest_path
    manifest: dict[str, str] | None = None

    if pdf_objects:
        manifest = load_manifest(manifest_path)
        # The checkpoint, not the manifest, decides what still needs
        # downloading: it is the thing that actually holds the ingested text.
        to_process = checkpoint.pending_objects(pdf_objects, ds.bucket, done_docs)
        skipped = len(pdf_objects) - len(to_process)
        if skipped:
            typer.echo(
                f"Skipping {skipped} PDFs already checkpointed (unchanged since last run)."
            )

        def _save(doc: DocumentRecord) -> None:
            checkpoint.append_document(paths, doc)
            done_docs[doc.id] = doc

        pdf_docs, manifest = ingest_pdf_full.run_full_ingest(
            s3,
            ds.bucket,
            to_process,
            manifest,
            settings.pdf_scratch_dir,
            settings.pdf_text_max_chars,
            settings.pdf_max_concurrency,
            on_document=_save,
        )

        empty = sum(1 for d in pdf_docs if not (d.text or "").strip())
        if empty:
            typer.echo(
                f"{empty} of {len(pdf_docs)} PDFs yielded no text (likely image-only)."
            )

    # The graph is rebuilt from the whole checkpoint, never from just this
    # run's results: build_graph writes a fresh graph every time, so feeding it
    # only the new documents would drop everything earlier runs ingested.
    documents = sorted(done_docs.values(), key=lambda d: d.source_uri)
    documents_df = documents_to_dataframe(documents, settings.kg_base_uri)
    properties_df = properties_to_dataframe(documents, settings.kg_base_uri)

    labels_df = entities_df = relations_df = None
    if settings.entity_extraction_enabled:
        from .entity_extraction import (
            extract_entities,
        )  # heavy import (kg-gen pulls in torch) — keep off the default path

        done_graphs = checkpoint.load_extractions(paths)
        to_extract = [
            d
            for d in documents
            if d.id not in done_graphs
            and (allowed_uris is None or d.source_uri in allowed_uris)
        ]
        if done_graphs:
            typer.echo(
                f"Skipping {len(done_graphs)} documents already extracted "
                f"(checkpointed); {len(to_extract)} to go."
            )

        typer.echo(
            f"Extracting entities/relations from {len(to_extract)} documents via "
            f"kg-gen ({settings.entity_extraction_model}, "
            f"chunk={settings.entity_extraction_chunk_size}, "
            f"cluster={'on' if settings.entity_extraction_cluster else 'OFF'}, "
            + (
                "no text cap"
                if settings.entity_extraction_max_chars <= 0
                else f"capped at {settings.entity_extraction_max_chars} chars/doc"
            )
            + ")..."
        )
        graphs = extract_entities(
            to_extract,
            model=settings.entity_extraction_model,
            api_key=settings.entity_extraction_api_key,
            temperature=settings.entity_extraction_temperature,
            max_chars=settings.entity_extraction_max_chars,
            context=settings.entity_extraction_context,
            chunk_size=settings.entity_extraction_chunk_size,
            cluster=settings.entity_extraction_cluster,
            on_graph=lambda g: checkpoint.append_extraction(paths, g),
        )
        for g in graphs:
            done_graphs[g.doc_id] = g
        graphs = list(done_graphs.values())

        labels_df = labels_to_dataframe(graphs, settings.kg_base_uri)
        entities_df = entities_to_dataframe(graphs, settings.kg_base_uri)
        relations_df = relations_to_dataframe(graphs, settings.kg_base_uri)
        typer.echo(
            f"Extracted {entities_df.height} document-scoped entities "
            f"({labels_df.height} distinct label forms), {relations_df.height} relations "
            f"from {len(graphs)} of {len(documents)} documents."
        )

    from .build_graph import (
        build_graph,
    )  # imported here: maplib import is heavy, keep off the --help path

    build_graph(
        documents_df,
        properties_df,
        settings.kg_base_uri,
        settings.kg_output_path,
        labels_df=labels_df,
        entities_df=entities_df,
        relations_df=relations_df,
    )
    typer.echo(
        f"Wrote {documents_df.height} document nodes, {properties_df.height} property "
        f"triples to {settings.kg_output_path}"
    )

    # Only now — the manifest must never claim a PDF is done before that PDF is
    # actually in the graph on disk. Entity extraction sits between ingestion
    # and this write and is the long, failure-prone phase (many LLM calls per
    # document, and SSO credentials expire mid-run). Saving the manifest before
    # it meant a crash there left the manifest saying "all done" and no graph
    # written at all: the next run found nothing pending and produced a graph
    # holding only the text files, with no error. The cost of saving late is
    # re-doing an interrupted run; the cost of saving early was a silently
    # incomplete graph.
    if manifest is not None:
        save_manifest(manifest_path, manifest)


@app.command()
def rebuild() -> None:
    """Rebuild the graph from the checkpoint alone — no S3, no LLM calls.

    Use after an interrupted run to get a complete graph from whatever has
    been ingested and extracted so far, or after changing KG_BASE_URI or a
    graph template, where the source data has not changed but the output has
    to be regenerated.
    """
    settings = _settings()
    paths = checkpoint.Paths.under(settings.checkpoint_dir)
    docs_by_id = checkpoint.load_documents(paths)
    if not docs_by_id:
        typer.secho(
            f"No checkpointed documents under {settings.checkpoint_dir} — run `build` first.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    documents = sorted(docs_by_id.values(), key=lambda d: d.source_uri)
    graphs = list(checkpoint.load_extractions(paths).values())
    typer.echo(
        f"Rebuilding from checkpoint: {len(documents)} documents, "
        f"{len(graphs)} with extractions."
    )

    documents_df = documents_to_dataframe(documents, settings.kg_base_uri)
    properties_df = properties_to_dataframe(documents, settings.kg_base_uri)
    labels_df = entities_df = relations_df = None
    if graphs:
        labels_df = labels_to_dataframe(graphs, settings.kg_base_uri)
        entities_df = entities_to_dataframe(graphs, settings.kg_base_uri)
        relations_df = relations_to_dataframe(graphs, settings.kg_base_uri)

    from .build_graph import build_graph

    build_graph(
        documents_df,
        properties_df,
        settings.kg_base_uri,
        settings.kg_output_path,
        labels_df=labels_df,
        entities_df=entities_df,
        relations_df=relations_df,
    )
    typer.echo(
        f"Wrote {documents_df.height} document nodes"
        + (
            f", {entities_df.height} entities, {relations_df.height} relations"
            if entities_df is not None
            else ""
        )
        + f" to {settings.kg_output_path}"
    )


@app.command()
def verify(
    sample: int = typer.Option(5, help="Number of sample retrieve() queries to run."),
) -> None:
    """Sample bedrock-agent-runtime.retrieve() and confirm each result's source URI
    resolves to a Document node already written to the graph."""
    settings = _settings()
    if not settings.kg_output_path.exists():
        typer.secho(
            f"{settings.kg_output_path} does not exist — run `build` first.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    from .verify import sample_and_check

    session = aws_clients.build_session(settings)
    bedrock_agent = aws_clients.bedrock_agent_client(session)
    runtime = aws_clients.bedrock_agent_runtime_client(session)
    kb = discover.get_knowledge_base(bedrock_agent, settings.kb_id)
    kb_type = kb["knowledgeBaseConfiguration"]["type"]

    matched, total = sample_and_check(
        runtime, settings.kb_id, kb_type, settings.kg_output_path, sample
    )
    typer.echo(f"{matched}/{total} sampled retrieve() source URIs found in the graph.")


@app.command()
def annotate(
    graph: Path = typer.Option(
        None, help="Graph to annotate. Defaults to KG_OUTPUT_PATH."
    ),
    vocabulary: Path = typer.Option(
        None,
        help="glossary/vocabulary.json. Defaults to ENERGY_VOCABULARY_PATH or a repo-relative search.",
    ),
    out: Path = typer.Option(
        None, help="Overlay to write. Defaults to <graph>.glossary.ttl."
    ),
    in_place: bool = typer.Option(
        False,
        "--in-place",
        help="Append to the graph instead of writing a separate overlay.",
    ),
) -> None:
    """Add glossary terms to an existing graph without rebuilding it.

    Reads the graph, writes an overlay of new triples beside it. Every existing entity,
    label and assertion id is left byte-identical — a second consumer pins those hashes.
    No network, no LLM, no checkpoint.
    """
    from . import annotate as annotate_mod

    settings = _settings()
    graph_path = graph or settings.kg_output_path
    if not graph_path.exists():
        typer.echo(f"no graph at {graph_path} — run build or rebuild first", err=True)
        raise typer.Exit(code=1)

    if vocabulary is None:
        try:
            from energy_vocab import Vocabulary

            vocabulary = Vocabulary.default_path()
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            typer.echo(f"cannot locate glossary/vocabulary.json: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    out_path = out or graph_path.with_suffix(".glossary.ttl")
    report = annotate_mod.annotate(graph_path, vocabulary, out_path, in_place=in_place)
    typer.echo(report.render())
    typer.echo(
        f"\nwrote {'graph in place: ' + str(graph_path) if in_place else str(out_path)}"
    )


@app.command()
def clean(
    graph: Path = typer.Option(
        None, help="Graph to clean. Defaults to KG_OUTPUT_PATH."
    ),
    vocabulary: Path = typer.Option(
        None,
        help="glossary/vocabulary.json. Defaults to ENERGY_VOCABULARY_PATH or a repo-relative search.",
    ),
    out: Path = typer.Option(
        None, help="Deduplicated graph to write. Defaults to <graph>.clean.ttl."
    ),
) -> None:
    """Fold duplicate predicates/entities into one and drop safely-orphaned nodes.

    Writes a separate, derived graph — <graph>.clean.ttl by default. The source graph is
    only read: every entity/label/assertion id in it is left exactly as it is, because a
    second consumer pins those hashes. No network, no LLM, no checkpoint.
    """
    from . import clean as clean_mod

    settings = _settings()
    graph_path = graph or settings.kg_output_path
    if not graph_path.exists():
        typer.echo(f"no graph at {graph_path} — run build or rebuild first", err=True)
        raise typer.Exit(code=1)

    if vocabulary is None:
        try:
            from energy_vocab import Vocabulary

            vocabulary = Vocabulary.default_path()
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            typer.echo(f"cannot locate glossary/vocabulary.json: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    out_path = out or graph_path.with_suffix(".clean.ttl")
    report = clean_mod.clean(graph_path, vocabulary, out_path)
    typer.echo(report.render())
    typer.echo(f"\nwrote {out_path}")


if __name__ == "__main__":
    app()
