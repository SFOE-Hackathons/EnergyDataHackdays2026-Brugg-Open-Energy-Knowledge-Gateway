# kg_creation — agent notes

Handoff doc for a future agent picking up this directory. Written
2026-09-07, last updated 2026-09-09 — verify against the code before
trusting anything below if it's been a while.

`.env` holds live credentials. Never read it out, echo it, or quote its
values into a transcript. `config.py` is the authority on what settings
exist and what they default to.

## What this is

`kg_creation` (package name `kg-creation`, CLI `kb-graph`) is a
standalone Python tool, **not part of the `events/` knowledge base content**
this repo otherwise holds. It ingests the AWS Bedrock Knowledge Base's
S3-backed source corpus (markdown/text always, PDFs with real text
extraction) and builds an RDF knowledge graph via
[`maplib`](https://github.com/DataTreehouse/maplib).

The KB this targets is the same one documented in
`docs/connecting-to-bedrock-knowledge-base.md` (MCP retrieval setup):
KB ID `KX3SMOOZ3U`, region `us-east-1`, **type `MANAGED`**, S3 bucket
`sfoe-data-energy-monitoring`. This tool does a different thing than that
doc — it downloads and parses the *source objects* directly from S3 to
build a graph, rather than querying the KB's retrieval API.

## Directory layout

```
kg_creation/
  kb_graph/            # the package (discover, inventory, ingest, transform, build_graph, verify, entity_extraction, checkpoint, annotate, cli)
  tests/                # pytest, pure-logic only, no live AWS/KB needed
  visualize_graph.py     # standalone script, renders a .ttl as interactive HTML (pyvis)
  pyproject.toml
  .env                   # live credentials. Gitignored. Never commit, never print, never paste into a transcript.
  data/                  # gitignored:
                         #   checkpoint/documents.jsonl    ingested documents incl. extracted text
                         #   checkpoint/extractions.jsonl  per-document entity/relation results
                         #   pdf-ingest-manifest.json      legacy ETag cache, informational only now
                         #   scratch/                      per-file temp download, emptied immediately
  output/                # NOT gitignored (only data/ is) — see "Known gaps"
  .venv/                 # gitignored, local virtualenv
```

`.env` sets `KG_OUTPUT_PATH=output/knowledge_graph.ttl`, which is why the
graph lands in `output/` rather than the `data/` default.

## The glossary overlay (`kb-graph annotate`)

`kb_graph/annotate.py` adds glossary terms to a graph that already exists. It reads the
`.ttl`, resolves every label form through `energy-vocab`, and writes a **separate overlay
file** — `knowledge_graph.glossary.ttl` — of new triples only.

```bash
kb-graph annotate                      # writes <graph>.glossary.ttl beside the graph
kb-graph annotate --in-place           # appends to the graph instead
```

No network, no LLM, no checkpoint, no maplib. About half a second over the 11-document
graph.

**It is a pure addition, and that is a hard constraint, not a preference.** Nothing is
rewritten: every `entity_id`, `label_id` and `assertion_id` stays byte-identical, because
the Open Energy Gateway pins those hashes (`oekg_core/ids.py` documents its `_normalize`
as a mirror of ours and its tests assert the values). Changing how ids are computed would
invalidate every entity IRI on both sides. So the glossary enters at the **lookup** layer,
never the id layer.

What the overlay says:

| Triple | On | Meaning |
|---|---|---|
| `kg:canonicalTerm evt:<slug>` | `:Label` and `:Entity` | this is glossary term X |
| `kg:termType ev:<type>` | `:Label` and `:Entity` | of one of the closed 7 types |
| `kg:matchRule` | `:Label` | how the match was made (`exact`, `ascii_fold`, `desuffix`, …) |
| `kg:contestedTerm evt:<slug>` | `:Label` | the label is claimed by more than one term; **no `canonicalTerm` is emitted** |
| `kg:canonicalPredicate kg:rel/<slug>` | the `relprop/` IRI | this raw predicate spelling means that canonical edge |
| `kg:publicationDate`, `kg:title` | `:Document` | parsed out of the S3 key |
| `kg:accordingTo kg:glossary/<hash>` | everything above | which vocabulary version said so |

Two behaviours worth knowing before you read the output:

- **A generic label produces no triple at all.** `Energie`, `Leistung`, `Speicher` resolve
  to real terms but are far too common in German prose to identify them; a marker on the
  node would only invite a consumer to treat it as a weak link.
- **Predicates are annotated, never rewritten.** Saying `relprop/Teil_von` means
  `rel/part-of` is 25 triples. Rewriting the assertions that use it would be 476 edits and
  would break the pure-addition contract.

The report prints an unresolved list ranked by `mentionCount`, filtered to labels that
could plausibly become terms. That is the glossary's work queue — it is how
`Kleinwasserkraft`, `GWh` and `Nachfrage` were identified as gaps.

### This directory is duplicated

PR #47 carries a byte-identical copy of this package at
`open-energy-gateway/kg-builder/` — same distribution name `kg-creation`, same `kb-graph`
console script. The two have diverged in both directions (this copy has `checkpoint.py`
and `mention_count`; that one has `--keys-file` and chunked extraction), and they cannot
both be installed into one environment.

The glossary work is deliberately confined to `annotate.py` plus one CLI command so that
copy can adopt it as a small diff. `energy-vocab` is declared **by name, not by relative
path**, because the two copies sit at different depths; set `ENERGY_VOCABULARY_PATH` where
`glossary/` is not a sibling.

### Known limitation: `rebuild` is not byte-reproducible

`checkpoint.py:157-158` reloads entities and relations into Python `set()`s, and string
hashing is randomised per process, so the first-wins surface-form picks at
`transform.py:75`, `80-81` and `103` vary between runs. The graph's *content* is stable;
which spelling becomes `kg:labelText` is not. The overlay does not inherit this — it is
built from sorted inputs and two runs produce an identical file.

## How to start it up

```bash
cd kg_creation
source .venv/bin/activate        # venv already exists in this checkout; if not: python3 -m venv .venv && pip install -e .
```

**`.env` is filled in and holds live credentials.** Do not read it out,
echo it, or quote its values anywhere — including into an agent transcript.
`config.py` is the authority on which variables exist and what they default
to; read that instead of the file.

The non-secret KB facts are documented in
`docs/connecting-to-bedrock-knowledge-base.md`, which is checked into the
repo: KB ID `KX3SMOOZ3U`, region `us-east-1`, type `MANAGED`, S3 bucket
`sfoe-data-energy-monitoring`. Confirm with the commands below if that doc
may have gone stale.

### Getting each `.env` value via AWS CLI

All read-only, all safe to run before `.env` is even filled in (they take
`--profile`/`--region` as flags, not from `.env`).

```bash
# AWS_PROFILE — which SSO profiles already exist locally
aws configure list-profiles
# no working profile yet? set one up per docs/connecting-to-bedrock-knowledge-base.md section 3:
#   aws configure sso --profile <name>   then   aws sso login --profile <name>

# AWS_REGION — same doc says the KB lives in us-east-1; confirm rather than assume
# (a wrong region returns empty results, not an error, so this is worth checking)

# BEDROCK_KB_ID — list all knowledge bases visible to this profile/region
aws bedrock-agent list-knowledge-bases \
  --profile <AWS_PROFILE> --region us-east-1 \
  --query 'knowledgeBaseSummaries[].{id:knowledgeBaseId,name:name}'

# confirm type (MANAGED vs classic/custom) for the one you picked
aws bedrock-agent get-knowledge-base \
  --knowledge-base-id <BEDROCK_KB_ID> --profile <AWS_PROFILE> --region us-east-1 \
  --query 'knowledgeBase.knowledgeBaseConfiguration.type'

# BEDROCK_KB_DATA_SOURCE_ID — list the KB's data source(s); most KBs have exactly one
aws bedrock-agent list-data-sources \
  --knowledge-base-id <BEDROCK_KB_ID> --profile <AWS_PROFILE> --region us-east-1 \
  --query 'dataSourceSummaries[].{id:dataSourceId,name:name,status:status}'

# S3_BUCKET_NAME — get-data-source, then dig the bucket out of whichever shape
# it comes back as (see discover.py's docstring — classic S3 vs managed connector):
aws bedrock-agent get-data-source \
  --knowledge-base-id <BEDROCK_KB_ID> --data-source-id <BEDROCK_KB_DATA_SOURCE_ID> \
  --profile <AWS_PROFILE> --region us-east-1 \
  --query 'dataSource.dataSourceConfiguration'
# classic S3: read .s3Configuration.bucketArn (bucket name is the part after the last ':')
# managed connector: .managedKnowledgeBaseConnectorConfiguration.connectorParameters is a
# JSON-encoded STRING, not a nested object — pipe through `| fromjson` (e.g. with jq) to
# get .connectionConfiguration.bucketName (and .bucketOwnerAccountId, worth checking for
# the cross-account case noted below)

# Fastest single-shot alternative to all three discovery calls above, once .env has
# AWS_PROFILE/AWS_REGION/BEDROCK_KB_ID(/BEDROCK_KB_DATA_SOURCE_ID) filled in:
python -m kb_graph.cli discover
```

`ENTITY_EXTRACTION_API_KEY` has no AWS command — it's an OpenAI API key
(or whatever provider `ENTITY_EXTRACTION_MODEL` names), get it from
whoever owns that account. Leaving it unset works too if the provider's
own env var is already exported (e.g. `OPENAI_API_KEY`) — see
`config.py`.

Run in this order (each is safe/cheap before the next; replace
`<AWS_PROFILE>` with whatever profile you filled into `.env`):

```bash
aws sts get-caller-identity --profile <AWS_PROFILE>   # 1. confirm creds resolve
python -m kb_graph.cli discover                        # 2. read-only: KB type + resolved S3 data source
python -m kb_graph.cli inventory                        # 3. read-only: full listing, counts/bytes by extension, no downloads
python -m kb_graph.cli build --limit 5                  # 4. small dry run — only 5 PDFs
python -m kb_graph.cli build                             # 5. full run — prompts for confirmation with total PDF size unless --yes
python -m kb_graph.cli rebuild                            # 6. optional: regenerate the graph from the checkpoint, no S3 / no LLM calls
python -m kb_graph.cli verify --sample 5                 # 7. sanity-check graph against sampled Bedrock retrieve() results
python visualize_graph.py                                 # 8. optional: reads output/knowledge_graph.ttl (per .env), writes an .html next to it
```

`build` is safe to re-run: it resumes from the checkpoint (see
"Resumability"), so an interrupted run costs only the documents it had not
finished.

Or via the installed console script: `kb-graph discover`, `kb-graph
inventory`, `kb-graph build`, `kb-graph rebuild`, `kb-graph verify` (same
commands, no `python
-m`).

Tests (no AWS/KB needed, pure logic only):

```bash
pip install -e ".[dev]"
pytest
```

## AWS access this needs

Profile: whatever `AWS_PROFILE` you fill into `.env`, region `us-east-1`. Same
account/profile family as the MCP retrieval setup in
`docs/connecting-to-bedrock-knowledge-base.md`, but **this tool needs more
permissions than that doc grants** — that doc only covers `bedrock:Retrieve`
+ discovery calls, not S3 object access.

Permissions actually exercised by the code (`kb_graph/aws_clients.py`,
`discover.py`, `inventory.py`, `ingest_pdf_full.py`, `ingest_text.py`,
`verify.py`):

| Action | Used by | Notes |
|---|---|---|
| `bedrock:GetKnowledgeBase` | `discover` | also used for KB-type detection in `verify` |
| `bedrock:ListDataSources` | `discover` (when no `BEDROCK_KB_DATA_SOURCE_ID` set) | skipped once `BEDROCK_KB_DATA_SOURCE_ID` is filled into `.env` |
| `bedrock:GetDataSource` | `discover` | resolves bucket/prefix |
| `bedrock-agent-runtime:Retrieve` | `verify` | same permission the MCP doc already covers |
| `s3:ListBucket` | `inventory`, `build` (via `list_objects_v2` paginator) | on the KB's bucket, `sfoe-data-energy-monitoring` |
| `s3:GetObject` | `build` (downloads text files, PDF bodies, and `<key>.metadata.json` sidecars) | **this is the new grant beyond the MCP doc's scope**  |

**Cross-account bucket risk**: `discover_cmd` in `cli.py` checks whether the
resolved bucket's owner account differs from the caller's own account and
prints a warning if so — a same-account IAM policy alone isn't enough in
that case, the bucket owner's account also needs a bucket policy granting
access. Read that warning if `discover` prints it; don't skip past it.

If you get `AccessDenied` on `list_objects_v2` or `get_object` with
`s3:ListBucket`/`s3:GetObject` already in your own role policy, this
cross-account case is the first thing to check.

## What the graph is for

The graph is a **term index over documents**, not a reasoning graph. The
question it answers is "which documents mention X, and how prominently" —
so an MCP client can narrow to a document set faster and more completely
than Bedrock's vector search, which returns top-k by similarity and can
never enumerate every match.

Read the schema that way:

| Node/edge | Index role |
|---|---|
| `:Label` (corpus-wide, normalized text) | **term** |
| `:Entity` (document-scoped) | **posting** — one (term, document) pair |
| `:mentions` (Document -> Entity) | document -> posting, the only edge stating it |
| `:mentionCount` on a posting | ranking signal |
| `:relprop/*` edges + `:Assertion` | optional disambiguation context, not the point |

An entity with no relation edges is not a defect — it is a perfectly good
index term. Don't "fix" it.

## Entity/relation extraction

Enabled via `ENTITY_EXTRACTION_ENABLED`, and it is **on** in this checkout.
A plain `build` therefore calls out to OpenAI via
[`kg-gen`](https://github.com/stair-lab/kg-gen), costing API credits and
adding `:Label`/`:Entity` nodes plus extracted relation triples. Turn it off
for an ingestion-only run. Per-document failures are logged and skipped;
they never abort the build (`entity_extraction.py`).

### Parameters

Defaults live in `config.py`; every one is overridable by environment
variable. Listed in the order they apply to a document's text.

| Variable | Default | What it does |
|---|---|---|
| `CHECKPOINT_DIR` | `data/checkpoint` | Where the per-document resume checkpoint lives. See "Resumability". |
| `PDF_TEXT_MAX_CHARS` | `500000` | Cap on text extracted from a PDF into `DocumentRecord.text` — the checkpoint and the extraction payload. Applies at ingest, before extraction sees anything. The text itself is **not** written to the graph; `:textTruncated` is how the `:Document` node reports that this cap bit. |
| `ENTITY_EXTRACTION_ENABLED` | `false` (on here) | Whether to run extraction at all. |
| `ENTITY_EXTRACTION_MODEL` | `openai/gpt-4o` | Any litellm-style model string kg-gen accepts. |
| `ENTITY_EXTRACTION_API_KEY` | unset | Provider key. If unset, the provider's own env var is used (e.g. `OPENAI_API_KEY`). |
| `ENTITY_EXTRACTION_TEMPERATURE` | `0.0` | Sampling temperature. |
| `ENTITY_EXTRACTION_MAX_CHARS` | `0` | **How much of the document is extracted at all.** `0` = no cap, whole text. Any positive value slices the head off and discards the rest. |
| `ENTITY_EXTRACTION_CHUNK_SIZE` | `5000` | How kg-gen divides whatever it received into LLM calls. Cuts on sentence boundaries, no overlap. Discards nothing. |
| `ENTITY_EXTRACTION_CLUSTER` | `true` | kg-gen's reconciliation pass (it defaults this **off**). Merges surface variants onto one representative, per document. |
| `ENTITY_EXTRACTION_CONTEXT` | `DEFAULT_EXTRACTION_CONTEXT` in `config.py` | Domain hint passed to kg-gen. Assumes an **English** corpus. |

Order matters and the two caps are easy to confuse:

```
PDF -> PDF_TEXT_MAX_CHARS -> doc.text -> ENTITY_EXTRACTION_MAX_CHARS -> payload
    -> ENTITY_EXTRACTION_CHUNK_SIZE -> N chunks -> 2 LLM calls each
```

### Why the defaults changed on 2026-09-09

- **`MAX_CHARS` 20000 -> 0.** The cap indexed only the first ~15% of each
  monitoring PDF (measured: 135,622 and 129,982 chars of text against a
  20,000-char cap). A capped term index is not a smaller index, it is a
  confidently incomplete one. Chunking alone would not have fixed this — it
  would have carved up only the first 20k.
- **`CHUNK_SIZE` introduced at 5000.** Two LLM calls per chunk (entities,
  then relations), so a 135k-char document is ~27 chunks / ~54 calls where
  it used to be 2. Cost per document rose roughly 27x. Smaller chunks cost
  more but recall more; larger chunks are cheaper and miss more.
- **`CLUSTER` on.** Without it `generate()` runs the entity pass and the
  relation pass independently and never reconciles them, so
  `"a hydropower reserve"` from the relation pass becomes a different term
  from `"hydropower reserve"` from the entity pass and the index fragments.
  In the 2026-09-04 build only 25 of 67 relation endpoints existed in the
  entity set. Adds further calls per document.
- **`CONTEXT` given a real default.** Names the domain and tells the model
  not to emit actions, verb phrases or bare dates as entities — the first
  build produced `"increase renewable energy use"` and `"2030"` as
  "entities". Assumes English: a German source document yields German
  terms, which will not match an English query, because term identity here
  is normalized text, not meaning.

### Operational notes

- Each document logs a line reporting entity/relation counts and how many
  relation endpoints were **not** in the entity set. Near zero means
  clustering is working; near 42/67 means it is not and the index will
  fragment. Read this on a `--limit` run before committing to a full build.
- kg-gen fires a document's chunk calls concurrently with an unbounded
  thread pool (~32 at once) and does not retry. A rate-limit error inside
  any chunk propagates, and `extract_entities` skips **the whole
  document** — logged, but that document contributes nothing to the index.
  Watch for skips on a low API tier.
- Size the full run: multiply observed `--limit 5` cost by the object count
  from `inventory`.

## Data flow / where files land

- **Never downloads the whole bucket at once.** `inventory`/listing is
  cheap pagination only. PDF bodies are streamed one at a time per worker
  (`PDF_MAX_CONCURRENCY=3`), extracted, then deleted immediately — peak
  disk usage is bounded by concurrency, not corpus size (~100GB estimated
  for the PDF portion).
- **Resumability**: a per-document checkpoint under
  `data/checkpoint/` (`CHECKPOINT_DIR`), written by `kb_graph/checkpoint.py`.
  Two append-only JSONL files:

  | File | Written when | Holds |
  |---|---|---|
  | `documents.jsonl` | an object finishes ingesting | the `DocumentRecord`, **including the extracted text** — so resuming never re-downloads from S3 |
  | `extractions.jsonl` | a document's extraction succeeds | the `ExtractedGraph` — the expensive artifact, ~54 LLM calls per document |

  Each record is one self-contained line, flushed and `fsync`ed on write. A
  run killed mid-write damages only the final line, and the loader skips a
  malformed trailing line with a warning rather than failing. `_append`
  terminates a newline-less orphan fragment before writing, because
  appending straight onto one fuses both records into a single unparseable
  line and loses the good one too.

  Replay keys records by document id, last line winning, so re-extracting a
  document appends rather than rewriting history.

  What this buys, concretely: interrupt a 10-document run after 6 and the
  next `build` re-downloads 0 files, re-extracts 4 documents, and writes a
  graph containing all 10.

  **The checkpoint, not the manifest, decides what still needs work.**
  `checkpoint.pending_objects` compares S3 ETags against checkpointed
  documents. `data/pdf-ingest-manifest.json` is still written (at the end of
  `build`) but is now informational — the checkpoint is the thing that
  actually holds the data, so "already done" and "recoverable without
  re-downloading" finally mean the same thing.

  To force reprocessing, delete the relevant checkpoint file. Deleting
  `extractions.jsonl` alone re-runs extraction without re-downloading — the
  right move after changing `ENTITY_EXTRACTION_MODEL`, `_CONTEXT`,
  `_CHUNK_SIZE` or `_CLUSTER`, since existing records were produced under the
  old settings and nothing invalidates them automatically.

- **The graph is always built from the whole checkpoint**, never from just
  the current run's results. `build_graph` writes a fresh graph every time
  and never merges with an existing `.ttl`, so feeding it only the newly
  processed documents would silently drop everything earlier runs ingested.
  This is also why `rebuild` exists: it regenerates the graph from the
  checkpoint with no S3 and no LLM calls, for use after an interruption or
  after changing `KG_BASE_URI` or a graph template.

- **Text extraction**: `pypdf` first, `pdfplumber` fallback when
  extraction looks too short. Image-only/scanned PDFs are not OCR'd —
  flagged via an `extractionWarning` property, not silently emptied.
- **Graph output**: `output/knowledge_graph.ttl` (per `.env`
  `KG_OUTPUT_PATH`) — one `:Document` node per ingested object
  (`sourceUri`, `contentType`, `sizeBytes`, `hasText`, `textTruncated` —
  no document body, see below), generic `:Property`
  edges from whatever metadata keys the source supplies, no fixed
  ontology. If entity extraction ran, additionally:
  - `:Entity` nodes, **scoped to one document each** (`:label`,
    `:hasLabelForm` a `:Label` node, `:mentionCount` occurrences of the label
    in that document's text). The same label in two documents is two entity
    nodes — see "Identity model" in `entity_extraction.py` for why. These are
    the index postings.
  - `:Label` nodes, one per normalized label form, shared corpus-wide. Group
    by these to get cross-document merging back as an explicit, reversible
    lexical join rather than a baked-in identity claim.
  - `:mentions` edges (Document -> Entity) — the document-to-content map,
    and the **only** predicate stating it. Emitted from the entity row in
    `graph_templates.py`, not from a table of its own, because the entity row
    already covers every posting including relation endpoints kg-gen names
    without listing as entities. An earlier `Mention` template with its own
    table iterated the narrower set and left a quarter of the postings with no
    edge, so `?doc :mentions ?e` and the entity's old `:derivedFrom`
    disagreed. `:derivedFrom` is now `:Assertion`'s alone.
  - Extracted relations written **twice**: as the plain traversable triple
    `subject predicate object`, and as a reified `:Assertion` node
    (`rdf:subject`/`rdf:predicate`/`rdf:object`) carrying `:derivedFrom`,
    `:extractionModel` and `:extractedAt`. The plain triple alone cannot say
    which document produced an edge; the assertion node is what makes edge
    provenance queryable. Costs ~7 extra triples per edge.
- **Visualization**: `visualize_graph.py` reads a `.ttl` and writes an
  interactive `.html` (currently `output/knowledge_graph.html`, 802KB) —
  one node per `:Document`, small shared linking nodes for common metadata
  values; long/unique literals like `sourceUri` stay in hover tooltips
  only, not as nodes. Document bodies are not in the graph at all.
  Open directly in a browser, no server needed. The layout is **static**: no
  physics simulation runs in the browser — node coordinates come from a
  seeded `networkx.spring_layout` computed at render time, and nodes are
  fixed and undraggable. Panning and zooming still work, and the same `.ttl`
  always renders the same picture.

## Known gaps / things to check before relying on this

- **`output/knowledge_graph.ttl` predates the single-`:mentions`-edge change
  (2026-09-09).** It was rebuilt on 2026-09-09 and *does* carry `:Assertion`
  (1331), `:Label` (1492), `:hasLabelForm` (1876) and `:mentionCount` (1876) —
  the earlier note claiming otherwise was itself stale. What is now out of
  date is the document-to-entity edge: it still has `:derivedFrom` on entities
  and only 1397 `:mentions` for 1876 entities. Re-run `build` to regenerate,
  and replace the copy on S3.
  It also holds only **2** `:Document` nodes — a `--limit`-style partial
  build, not a corpus run. Do not read an absent term in it as an absent term
  in the corpus.

- **The checkpoint is half there.** `CHECKPOINT_DIR` is unset, so it
  defaults to `data/checkpoint/`. `documents.jsonl` is present (2 documents,
  with their text), but **`extractions.jsonl` is missing** — so `rebuild`
  writes the 2 `:Document` nodes and **zero entities**, and the
  ~54-LLM-call-per-document extraction artifact for those PDFs is lost.
  Restoring the term layer means a fresh `build`, paying the LLM calls again
  (the S3 downloads are skipped, since `documents.jsonl` holds the text).

- **`documents.jsonl` is now the only persisted copy of the document
  bodies.** The graph no longer carries `:text`, so deleting
  `data/checkpoint/` loses the text outright and the next `build` must
  re-download from S3. That was already the intended source of truth
  (`checkpoint.py`: "the thing that actually holds the ingested text"), but
  it used to have the `.ttl` as an accidental backup. It no longer does.

- **Term identity across documents is normalized text only.** `label_id`
  lowercases and strips whitespace, nothing more. kg-gen's clustering merges
  surface variants *within* a document, but nothing merges
  `"the hydropower reserve"` in one document with `"hydropower reserve"` in
  another — those remain two `:Label` nodes, so `find_documents` on one
  misses the other. Stripping leading articles in `_normalize` would have
  merged 3 of 42 unreconciled endpoints in the 2026-09-04 build; the rest
  needed clustering. Revisit once there are real corpus-wide numbers.

- **`output/` is not gitignored** (only `data/` and `.venv/` are, per the
  repo's root `.gitignore`). The generated `knowledge_graph.ttl` (297KB)
  and `knowledge_graph.html` (802KB) currently sit there as untracked
  files that `git add -A` would happily stage. Decide deliberately whether
  these should be committed, gitignored, or moved under `data/` before
  staging anything in this directory — don't let a broad `git add` sweep
  them in by accident.
- This whole directory (`kg_creation/`) is untracked (`git status` shows
  `?? kg_creation/`) — nothing here has been committed yet. Follow the
  root `CLAUDE.md` / `.claude/rules/git-workflow.md` workflow (branch,
  scoped commit, PR) when first adding it, same as any other change in
  this repo.
- `maplib`'s public API surface (`Model`, `Template`/`Parameter`/
  `Variable`/`Triple`, `.map()`, `.write()`) was hand-verified against the
  pinned version in `pyproject.toml` when this was built — re-check if
  that pin is bumped.
- Only S3-backed Bedrock KB data sources are handled
  (`discover.py`/`UnsupportedDataSource`) — other connector types
  (SharePoint, Confluence, web crawler, Google Drive, OneDrive) raise, not
  silently skip.
