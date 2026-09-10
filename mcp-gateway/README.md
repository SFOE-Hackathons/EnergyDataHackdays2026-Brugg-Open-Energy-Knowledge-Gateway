# Open Energy Knowledge Gateway — MCP Server

An MCP server exposing official Swiss federal energy publications (Swiss Federal
Office of Energy, SFOE / BFE) as structured, source-attributed tools that any
MCP-compatible client can consume.

The server itself does no inbound authentication, so it can be run locally and
pointed at with no credential setup. Deployed, it has no endpoint at all: it
runs as a Lambda that only an AgentCore Gateway may invoke. The gateway is the
public face and is open — no token, no signup, just a URL — see
[Deploying](#deploying).

## Source attribution

Every result from every tool resolves to the same `source` object. Two fields
make a result citable by whoever reads it:

- **`published_at`** — the document's publication date, in ISO form.
- **`download_url`** — a permanent, public link to the original PDF on the SFOE
  publication database, openable by anyone with no credentials.

`download_url` is `null` when a document could not be matched to a public
record with confidence. That is deliberate: a link to the wrong publication is
worse than no link, because a wrong citation does not look wrong to the reader.
Roughly 6% of the corpus currently resolves to `null`. Cite those by `title`
and `published_at`.

### Sources are interned, not inlined

Attribution is **not** repeated on each entry. Every response carries a
top-level `sources` object mapping a short id to one document, and each entry
carries a `source_id` naming its document:

```jsonc
{
  "sources": {
    // source_fields="core" (the default on every tool)
    "s1": {
      "title": "2025-08-01_fakten-zur-windenergie.pdf",
      "published_at": "2025-08-01",
      "download_url": "https://pubdb.bfe.admin.ch/de/publication/download/12259",
      "media_type": "image"       // omitted when null, which is ~4 records in 5
    }
  },
  "data": [
    { "source_id": "s1", "score": 0.61, "years_covered": [2018, 2024] }
  ]
}
```

Results are drawn from far fewer documents than there are entries — a measured
5-year timeline returns 62 passages from **34** documents — so inlining meant
sending the same title and URL two to five times each.

Two entries share an id only when their source records are **equal**. That is
deliberately strict: the corpus files some publications under more than one
document name, and those are genuinely different attributions even when they
point at the same PDF. Merging them would report a title the passage was not
filed under.

Ids are assigned in order of first appearance and are meaningful only within
the response that produced them. Never persist an id or carry it between calls.

### `source_fields` — `"core"` (default) or `"all"`

Every tool takes a `source_fields` parameter. `"core"` omits four fields that
carry no information about the documents; `"all"` returns them. Measured over
666 source records spanning 160 documents:

| field | distinct values | why `"core"` omits it |
|---|---|---|
| `file_type` | **1** (`"PDF"`) | constant |
| `language` | **1** (`"en"`) | constant — *and wrong*, the corpus is largely German |
| `created_at` | — | byte-identical to `last_updated_at` on **every** record |
| `last_updated_at` | 70 | all 70 inside the same *minute* (`2026-08-31T13:07:xx`) — it is when our knowledge base ingested the corpus in one batch, not when the document changed |

The last one is the trap: `last_updated_at` reads like document metadata and is
not. `published_at` is the field that carries real document time. Together the
four cost ~72 KB on a wide timeline and say nothing. They stay available at
`"all"`, because "constant today" is an observation about the current corpus,
not a guarantee about the next one.

> **`download_url` is never dropped, at either setting, even when `null`.** It
> is what makes a cited figure checkable by someone who does not have access to
> this server, and a `null` is itself information — "no confident public match"
> has to be distinguishable from "the field was trimmed away".

A `"core"` field that is `null` for a given document is omitted rather than
sent as an explicit null; an absent key says the same thing for fewer bytes.
`download_url` is again the exception.

## Passage annotations

The knowledge base returns text with no indication of what its numbers *mean*.
The same magnitude — 704.9 MW — appears in one document as photovoltaic
capacity **sold** and in another as capacity **installed**; elsewhere the split
is between capacity added in one year and the cumulative stock at year end.
Nothing upstream separates these, so two passages can look comparable while
measuring different things.

Every result from every tool therefore carries these fields, derived from the
passage text in `semantics.py`:

| Field | Meaning |
|---|---|
| `bases` | What the numbers measure: `sales`, `installed_annual`, `installed_cumulative`, `production`. A **list**, each entry `{basis, evidence}` |
| `years_covered` | `{min, max, years}` — the years the passage's own content names |
| `is_projection` | The passage carries forward-looking figures, not only measured ones |
| `is_truncated` | The upstream chunker cut the passage mid-content |
| `truncation_reasons` | Why it was flagged |

Three properties are deliberate and worth not "simplifying" away:

- **`bases` is a list, not a single label.** One corpus chunk states PV systems
  sold, the 90 % of those assumed installed, *and* a running total. Collapsing
  that to one value would invent a distinction the source never made.
- **An empty `bases` means the text did not say.** `installiert` is the most
  common marker in the corpus and the least specific — on its own it cannot
  separate this year's additions from the total — so it is not a marker at all.
  A wrong basis is worse than no basis, because it reads as authoritative.
- **`years_covered` is not `queried_years`.** A passage retrieved by a query for
  2021 routinely holds a 2002–2022 series. `queried_years` (timeline tool only)
  records what was *asked*; never group or attribute figures by it. It holds
  more than one year when the same passage was the best match for several of
  them, which is common and is not an error.

## Tools

### `search_energy_knowledge`

Ad-hoc semantic search. Returns ranked, verbatim passages.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `query` | string | — | Natural-language search query |
| `max_results` | int | 10 | 1–25 |
| `source_fields` | `"core"` \| `"all"` | `"core"` | see [`source_fields`](#source_fields--core-default-or-all) |

Returns `{result_count, sources, results}`, each result `{score, text,
source_id}` plus the annotations above — resolve `source_id` against the
top-level `sources` object. Results are deduplicated before truncation, so
`max_results` distinct passages come back rather than `max_results` slots that
repeats may partly fill — see [Deduplication](#deduplication).

### `get_metric_timeline`

Queries once per year across a range, returning a flat array tagged with
`queried_years` — intended for tracking a metric's development over time.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `metric` | string | — | e.g. `"solar PV installed capacity"` |
| `start_year` | int | — | inclusive |
| `end_year` | int | — | inclusive; **at most 5 years per call** |
| `max_results_per_year` | int | 3 | 1–25 |
| `detail` | `"index"` \| `"full"` | `"index"` | whether to include passage text |
| `source_fields` | `"core"` \| `"all"` | `"core"` | see [`source_fields`](#source_fields--core-default-or-all) |

Returns `{metric, start_year, end_year, detail, source_fields, result_count,
duplicates_collapsed, sources, data}`, where each `data` entry is a search
result plus `queried_years`.

> `queried_years` records only what was asked, **not** what the passage
> contains. Use `years_covered` for anything that groups, charts or attributes
> figures by year; treat `queried_years` as a retrieval trace.

#### `detail` — the verbatim text is opt-in

A timeline is the largest response this server produces, and the passage text
is over half of it. `detail` defaults to `"index"`, which returns every
annotation and full source attribution but **omits `text`**. Measured on a
5-year, 25-per-year request (the widest a single call allows):

| | `source_fields="core"` | `source_fields="all"` |
|---|---|---|
| `detail="index"` (both defaults) | **~42 KB** | ~48 KB |
| `detail="full"` | ~94 KB | ~101 KB |

Field trimming and interning are separable; measured against one identical
index response of 62 entries from 34 documents, in compact JSON:

| | inlined sources | interned sources |
|---|---|---|
| `source_fields="all"` | 41.1 KB | 31.9 KB |
| `source_fields="core"` | 32.8 KB | **27.4 KB** |

Interning is the larger of the two and, unlike dropping a field, costs nothing:
every entry still resolves to its full attribution in one lookup.

The index is meant to be read first: it says which documents cover which years
and which passages carry figures, which is enough to decide what is worth
reading. Then call again with `detail="full"` over a narrower range to get the
text for just those years.

This is a **breaking change for any caller that expected `text`** — an existing
client that does not pass `detail` now gets the index. That is deliberate: the
previous default was the expensive one.

#### Deduplication

Passages with identical text are collapsed into one entry, keeping the
highest-scoring copy, and the `queried_year`s of every copy are merged into
`queried_years`. `duplicates_collapsed` reports how many entries this removed
(63 of 125 on the request above — adjacent years overlap heavily).

This happens at two levels, because they catch different things:

- **Within one retrieval** (`KnowledgeBaseClient._normalize`), *before* the
  `max_results` truncation. The corpus stores some publications under more than
  one document name, so a single query retrieves the same passage twice.
  Deduplicating after truncation would return fewer results than asked for;
  doing it before lets the next distinct passage take the freed slot. A
  `max_results=25` search that previously yielded 20 distinct passages now
  yields 25.
- **Across the per-year retrievals** (`get_metric_timeline`), which the client
  cannot see across. Adjacent years return heavily overlapping passages.

Note that this mostly buys **information density, not bytes**: because each
year now returns 25 *distinct* passages rather than 25 with repeats, a full
timeline is about the same size as before while carrying more distinct content.

Still deliberately **not** done, and left to the calling agent:

- **No numeric extraction.** Figures stay inside the passage text.
- **No reconciliation across sources.** Two *different* passages may report the
  same year differently; both are returned so the caller can compare them.
  Only byte-identical passages are collapsed.
- **No visualization.** Charting is the client's concern.

#### At most 5 years per call

`MetricTimelineTool.MAX_TIMELINE_YEARS` caps the range; a wider request is
rejected with a message naming the limit. Cover a longer span with several
calls.

The cap bounds two things at once. Each year is a separate sequential query, so
it bounds latency (a 5-year, 25-per-year request takes ~4–9 s, the spread being
the public-source resolver's cache warming). It also bounds the fan-out a
single inbound call causes against the knowledge base — which is the real
exposure once this server is publicly reachable, since the hosting bill is not
where the cost lands.

Little is lost by it: adjacent years retrieve heavily overlapping passages, and
a passage routinely covers a decade of its own — see `years_covered`. Half of a
5-year request already collapses as duplicates.

A JSON Schema cannot express "`end_year - start_year` < 5", so the cap is also
stated in the tool description. Without that, the only way a model discovers it
is by tripping it.

### `get_chart_data`

Returns numeric series lifted out of charts and tables, ready to plot.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `topic` | string | — | e.g. `"Windenergie Produktion"` |
| `max_charts` | int | 5 | 1–15 |
| `source_fields` | `"core"` \| `"all"` | `"core"` | see [`source_fields`](#source_fields--core-default-or-all) |

Returns `{topic, probes_run, source_fields, chart_count, sources, charts}`.
Each chart:

```jsonc
{
  "title": null,
  "columns": ["Year", "Wind_Energy_Production_TJ/a"],
  "column_bases": {"Year": [],
                   "Wind_Energy_Production_TJ/a": [{"basis": "production",
                                                    "evidence": "Production"}]},
  "years_covered": {"min": 1990, "max": 2024, "years": [1990, 1992]},
  "precision": "estimated",
  "is_estimate": true,
  "extraction_method": "chart_visual_read",
  "is_projection": false,
  "is_truncated": false,
  "note": "Note: These values are estimated based on visual interpretation…",
  "row_count": 35,
  "rows": [{"Year": {"raw": "1990", "value": 1990.0, "min": null, "max": null, "kind": "exact"},
            "Wind_Energy_Production_TJ/a": {"raw": "2", "value": 2.0, "min": null, "max": null, "kind": "exact"}}],
  "raw_block": "Year,Wind_Energy_Production_TJ/a 1990,2 …",
  "score": 0.51,
  "source_id": "s3"
}
```

**`precision` is the field that matters.** `"exact"` means the numbers were
printed on the figure as data labels. `"estimated"` means they were read off
the figure by eye — either given as a `min`/`max` range, or delivered as
single numbers that the transcription's own `note` admits are estimates.
Callers must not present estimated values as precise figures, must not invent
the midpoint of a range, and should surface `note` alongside an estimated
series. `is_estimate` is the boolean form, and `extraction_method` says how the
numbers were obtained — `table`, `chart_label`, `chart_visual_read`, or `null`
when the transcription does not say. It is deliberately `null` rather than
defaulting to `table`: an unearned `table` would launder an eyeballed number
into an official published figure.

`column_bases` is keyed per column, not per chart, because a single figure
routinely plots annual additions against cumulative stock. The label column
(`Year`) gets no basis. Passage context is consulted **only** for
single-series charts — on a two-column chart it labelled the
`Annual_Installation_MW_Jahr` column `installed_cumulative`, exactly the
confusion the field exists to catch.

`years_covered` is read from the parsed row labels, not by scanning the block
text. A raw `<data>` block is bare CSV, so a four-digit measurement sits beside
a four-digit year: `1994,1900` and `2018,1945` made text scanning report a wind
chart as spanning 1900–2100.

Coverage is opportunistic: only a minority of figures were transcribed with
usable data. A topic returning zero charts is a normal outcome, not an error —
fall back to `search_energy_knowledge`.

The probe templates matter more than they look. The publications are German but
the `<data>` blocks are transcribed in **English** (`Year,Installed PV Capacity
(MW)`), so an all-German probe set retrieves the prose around a figure while
missing the chunk holding its numbers. Measured across seven topics, German-only
probes found 3 charts where the current mixed set finds 30. Re-tune
`QUERY_PROBE_TEMPLATES` only against a spread of topics, never by assumption.

## Known limitations

- **No page numbers.** The corpus carries no page-level metadata, so results
  cannot be cited by page. Fixing this would require re-ingesting the knowledge
  base with a parser that preserves page anchors — outside this service.
- **`language` is unreliable.** Most documents are German but are tagged `"en"`.
- **Chart data is sparse.** See `get_chart_data` above.
- **Upstream chunking splits on size, not structure.** Chunks routinely open a
  tag and end before closing it — `<extraction>` was observed opening three
  times and closing once in a single sample, and `</relationships>` appears
  with no opener. A `<data>` block can be severed mid-value. The gateway cannot
  fix the chunking; it recovers the complete rows of a partial block and flags
  the passage with `is_truncated` instead of presenting it as whole. Detection
  is heuristic: a flagged passage deserves suspicion, an unflagged one is not a
  guarantee. A real fix means re-ingesting with structure-aware chunking — and
  that is currently not possible: the knowledge base is a **MANAGED** Bedrock
  knowledge base, whose chunking, embedding model and vector store are all
  service-operated and none of them configurable. Fixing chunking means first
  migrating to a customer-managed vector knowledge base.
- **The corpus contains duplicates.** 293 of 2430 source PDFs (12%) are stored
  twice, the second copy under a filename truncated to 115 characters. Both
  copies are indexed, so one document can occupy two result slots with
  identical text. Ingestion-side; not fixable from this service.
- **A figure's `<title>`/`<caption>` often land in a different chunk than its
  `<data>`**, which is why `title` is frequently `null` on returned charts.
- Passages are extracted from PDFs including chart/table descriptions, so text
  quality varies.

## The gateway in front of it

The AgentCore Gateway is defined by `infra/create-gateway.sh` at the repo root,
which is idempotent — it creates the gateway and its target, or brings an
existing pair back in line with what the script says.

```bash
./infra/create-gateway.sh --show   # print current state, change nothing
./infra/create-gateway.sh          # create or update
```

The gateway holds a **static copy of this server's tool catalogue**, because a
Lambda target has no way to discover one — see [Why the gateway invokes the
Lambda](#why-the-gateway-invokes-the-lambda-instead-of-calling-it). The script
generates that copy by importing this server (`tool_schema.py`), so it cannot
be edited into drift by hand — but it does mean a tool change is only live once
**both** scripts have run, in order, from the same checkout. Deploying alone
leaves the new tool running on the Lambda and invisible through the gateway.

Because the catalogue is generated from an import, `create-gateway.sh` needs an
interpreter with `requirements.txt` installed. Point it at one with
`PYTHON=.venv/bin/python ./infra/create-gateway.sh` if `python3` is not it.

The gateway no longer configures retrieval — it did when it sat upstream of this
server, and does not now. See [Retrieval breadth](#retrieval-breadth).

## Running

Needs AWS credentials with `bedrock:Retrieve` on the knowledge base — a profile,
an instance role, or keys in a gitignored `.env` at the repo root. No Cognito
secret: this server holds none.

To serve MCP over HTTP locally, run the server directly — from the repo root:

```bash
.venv/bin/python mcp-gateway/dev_run.py
```

Endpoint: `http://localhost:8000/mcp` (streamable-HTTP).

The container image is **not** an HTTP server. It builds on the AWS Lambda base
image and its entrypoint answers Lambda invocations, so `docker run -p 8000:8000`
gets you the runtime interface emulator, not this server. Build it to test what
ships, not to develop against:

```bash
docker build -t open-energy-knowledge-gateway ./mcp-gateway
```

Tests (no network, no credentials needed):

```bash
.venv/bin/python -m pytest mcp-gateway/tests/ -q
```

### Claude Code

`.mcp.json` at the repo root registers the server as `energy-knowledge-gateway`.
Approve it once when prompted, then the tools appear natively.

## Deploying

Two idempotent scripts, in this order. Re-running either is how you ship a
change.

```bash
./infra/deploy-lambda.sh            # build, push, create or update, verify
./infra/deploy-lambda.sh --show     # print current state, change nothing
./infra/deploy-lambda.sh --pause    # kill switch: refuse all traffic
./infra/deploy-lambda.sh --resume   # undo --pause
./infra/deploy-lambda.sh --destroy  # tear down everything it created

./infra/create-gateway.sh           # point the gateway at it
```

`deploy-lambda.sh` puts this image on Lambda with **no URL and no HTTP route**.
Its resource policy grants `lambda:InvokeFunction` to exactly one principal —
the AgentCore Gateway's role — and the same grant is attached identity-side to
that role. `create-gateway.sh` registers the function as an **`mcp.lambda`**
target with a `GATEWAY_IAM_ROLE` credential provider and the generated tool
catalogue inline.

The endpoint to hand out is the **gateway** URL, and that is all a client needs
— it takes no credential. The function underneath is not addressable at all.

### Why the gateway invokes the Lambda instead of calling it

An `mcp.mcpServer` target pointed at an `AWS_IAM` Function URL is the design
this obviously wants: the gateway speaks MCP, this server speaks MCP, nothing
in between needs to know anything. It was built that way and it does not work.

**AgentCore's outbound SigV4 signer computes the signature over an empty
payload hash while sending the request body.** Anything that verifies the
payload hash — a Lambda Function URL, API Gateway with IAM auth — rejects every
request with a signature mismatch *before* invoking anything. MCP's streamable
HTTP transport is POST-only, so there is no request shape that escapes it.

What the evidence looked like, in case it recurs elsewhere:

| observation | |
|---|---|
| target status | `UPDATE_UNSUCCESSFUL`, "Authorization error when sending message" |
| CloudWatch | `UrlRequestCount 2`, `Url4xxCount 2`, `Invocations 0` |
| same handshake, hand-signed | `200` / `202` / `200`, repeatably |
| adding an identity-side grant | no change |
| signing over `b""` locally while sending a body | reproduces the 403 exactly |

Independently reported on AWS re:Post ("Bedrock AgentCore Gateway - API Gateway
POST requests fail (SigV4 auth)"). It is a platform defect, not a
misconfiguration, and no IAM arrangement fixes it.

A Lambda target sidesteps the signer: the gateway calls the Lambda Invoke API,
passing the tool's arguments as the event and the tool's name in the client
context. `lambda_handler.py` is this side of that contract. It dispatches
through the same `MCPServer.call_tool` the HTTP transport uses rather than
calling tool functions directly, so argument validation, defaulting and error
translation stay identical across both entrypoints.

The cost is the tool catalogue: a Lambda target carries a static copy instead of
discovering one, which is why deploying and publishing are now two steps that
must both run. `tool_schema.py` generates that copy from this server, folding
the constraints AgentCore's schema dialect cannot express (`enum`, `minimum`,
`maximum`, `default`) into each property's description rather than dropping them
— a model that cannot see `enum: ["core", "all"]` will invent a third value.

**Why Lambda.** It is the only remaining option with an enforceable numeric
concurrency ceiling, and the only one that costs nothing while idle. AgentCore
Runtime would otherwise be the natural host — this image already matches its
container contract exactly — but it has no concurrency cap of that kind. App
Runner has been closed to new customers since 2026-04-30. The runner-up is ECS
Express Mode (App Runner's successor): one create call, zero code changes, but
~$30/mo standing and an amd64 cross-build.

**On the image.** It builds on `public.ecr.aws/lambda/python:3.12` and answers
invocations rather than serving a port. An earlier revision used
`python:3.12-slim` plus the Lambda Web Adapter so that one image could serve
both HTTP locally and a Function URL on Lambda; with the Function URL gone
there is no second deployment for one image to serve. Local HTTP is unaffected
and does not need the image at all — `dev_run.py` runs `server.py` on the host.

### Why the endpoint is open

The goal was always a genuinely open endpoint — no credentials, no signup, point
an MCP client at a URL. It took four designs to get there, three of which failed
for unrelated reasons, and for a while this section argued the opposite. That
argument was wrong on a point of fact and the correction is recorded below.

The obvious open design is a Function URL with `--auth-type NONE` and a resource
policy granting `lambda:InvokeFunctionUrl` to `Principal: "*"`. It was deployed
exactly that way first. Every request returned **403 `AccessDeniedException`**,
and **no CloudWatch log group was ever created** — the denial happens at the
authorization layer, before the function runs. A direct `lambda invoke` with a
synthetic Function URL v2.0 event returned a perfectly good MCP response over
the same image, so the function, the arm64 build, the web adapter it carried at
the time and the policy were all fine.

The account is a member of AWS Organization `o-3r6tcb49g2` (BFE), whose
guardrails a member account cannot read. Their shape shows up indirectly:

```text
An error occurred (AccessDeniedException) when calling the CreateApi operation:
User: arn:aws:iam::542202863496:user/user08 is not authorized to perform:
apigateway:POST ... with an explicit deny in a service control policy:
arn:aws:organizations::429128461717:policy/o-3r6tcb49g2/service_control_policy/p-bvj3x4lc
```

Probing the account for a public entry point:

| service | |
|---|---|
| Lambda, ECS, EC2, S3, CloudFront | allowed |
| API Gateway, ALB, App Runner, Lightsail, Amplify | **denied by SCP** |

That eliminates every conventional front door at once. CloudFront survived, and
an intermediate design used it: with an **Origin Access Control of type
`lambda`** it signs each origin request as the `cloudfront.amazonaws.com`
service principal, leaving no anonymous Lambda invocation to deny while the
viewer side stayed completely open. It was built, deployed and worked.

It was then torn down, because the AgentCore Gateway is a better answer to the
same question and one this project already had. The comparison:

| | CloudFront + OAC | AgentCore Gateway |
|---|---|---|
| caller needs credentials | no | no (`authorizerType: NONE`) |
| requests per search | 1 | 1 |
| optional per-client identity | no | yes — switch to `CUSTOM_JWT` |
| extra AWS moving parts | distribution, OAC, edge signer | none beyond the gateway |

#### The correction

For most of the build this section claimed the gateway *could not* be open —
that reaching it necessarily took a Cognito token, and that giving up anonymity
was the price of using it. That was false. `CreateGateway.authorizerType`
accepts **`NONE`**, documented as "No authorization", alongside `CUSTOM_JWT` and
`AWS_IAM`; `authorizerConfiguration` is required only for `CUSTOM_JWT`. The
gateway was deployed with `CUSTOM_JWT` because that is what the examples showed,
and the constraint was assumed rather than checked against the service model.

Everything the old argument said about *consequences* still holds and is kept
below under [The concurrency cap is not optional](#the-concurrency-cap-is-not-optional):
with no caller identity there is no per-caller brake, a runaway client and a
popular launch look identical, and the only response to either is to shut
everyone off. The difference is that this is now a cost being accepted for open
access, not a benefit being claimed for closing it.

**A gateway's authorizer cannot be changed after it is created.** `UpdateGateway`
rejects it outright:

```text
ValidationException: Authorizer type cannot be updated for an existing gateway
```

So going open was not a setting change, it was a new gateway — and since the URL
is built from the generated gateway id, any route to `NONE` changes the URL.
Given that, the open gateway `bfe-energy-knowledge-open` was stood up *beside*
the original `bfe-energy-knowledge-gateway` rather than replacing it, so every
client holding the old JWT URL kept working. The old one is disposable once
nothing needs it.

Reverting means the same thing in reverse — another gateway, another URL:

```bash
GATEWAY_NAME=bfe-energy-knowledge-jwt AUTHORIZER_TYPE=CUSTOM_JWT \
  ./infra/create-gateway.sh
```

The Cognito pool and app client are kept deployed for exactly that reason.
`create-gateway.sh` refuses to delete a gateway on its own; it prints the
`delete-gateway` command and the URL warning and stops.

### The concurrency cap is not optional

The hosting bill is not the exposure. **Every inbound request fans out into
Bedrock retrievals on this account** — up to 5 for `get_metric_timeline` (one
per year, which is what `MAX_TIMELINE_YEARS` bounds) and 5 for
`get_chart_data`. That cost is identical whatever hosts the proxy, so capping
the container's price caps only the container.

Lambda's default account concurrency is **1000**. Uncapped, AWS will autoscale
on a caller's behalf to as many as 1000 concurrent invocations × 5 upstream
retrievals = **5000 concurrent Bedrock calls**, and the first sign of it is the
bill. Scale-to-zero and unbounded scale-up are the same mechanism.

Authentication would narrow who can start that, but would not cap it: one
Cognito client with a `for` loop scales exactly as fast as an anonymous one. The
cap is the brake either way. With the endpoint open there is no second brake and
no way to tell one caller from another, so **the reserved concurrency setting is
the only thing between a retry loop and an unbounded Bedrock bill** — which is
why it is set before the gateway is created rather than after.

The script therefore sets reserved concurrency (default 5) **before anything
public is created**, and that ordering is deliberate. `--pause` sets it to 0, which
rejects every request before any code runs and before any Bedrock call is made;
it is instant, fully reversible, and destroys nothing.

Reserved concurrency is a limit on **simultaneous requests, not on request
rate**. AWS's "max rps = 10× reserved concurrency" holds only for sub-100 ms
functions; here throughput is concurrency ÷ duration, and these requests are
slow:

| tool | measured | sustained throughput at 5 slots |
|---|---|---|
| `search_energy_knowledge` | ~0.7–1.0 s | ~5–7 req/s |
| `get_chart_data` | ~6–9 s | ~0.6–0.8 req/s |
| `get_metric_timeline` (5 yr × 25) | ~4–9 s | ~0.6–1.2 req/s |

There is no queue: request 6 gets an immediate 429. Twenty people calling the
timeline tool at once means five run and fifteen fail — worth raising for a
demo window and dropping back afterwards, remembering each slot is worth up to
5 concurrent Bedrock calls:

```bash
RESERVED_CONCURRENCY=15 ./infra/deploy-lambda.sh --resume
```

### Memory is a CPU setting

`MEMORY_MB` defaults to 512 and is a compromise, not a measurement of need. The
working set is small and settled — measured on this image under three
concurrent worst-case requests (`detail=full`, `source_fields=all`, 5 yr × 25):
**53 MB idle, 76 MB peak**, ~19% of one core for all three. Lambda runs one
request per execution environment, so the applicable figure is ~65 MB.

Lambda ties CPU to memory linearly, with a full vCPU at 1769 MB. In steady
state this function is I/O-bound — it waits on the gateway — so more CPU does
not shorten a request, and billing is per GB-second, meaning over-provisioning
multiplies the bill for identical wall clock. But **cold start is CPU-bound**:
unpacking a ~204 MB image and importing `mcp`/`pydantic`/`starlette` is
real work. Steady state wants 128 MB; cold start wants more.

Settle it with data on the first deploy rather than guessing twice — every
invocation logs both numbers:

```bash
aws logs tail /aws/lambda/bfe-mcp-gateway --region eu-central-1 --filter-pattern REPORT
```

`Max Memory Used` is the true working set (expect ~80–120 MB, since Lambda's
runtime overhead sits on top of the figures above) and `Init Duration` is the
cold start. Comfortable init → halve it; slow init → raise it.

Other simultaneity limits, none of them close to binding: the account default
is 1000 concurrent executions region-wide (reserving 5 carves them out of that
pool; AWS requires ≥100 left unreserved), the per-request timeout is set to 60 s
against a ~9 s worst case, and the synchronous invocation response cap is 6 MB
against a ~101 KB largest response. Behind an ALB that cap would have been 1 MB,
which is one more reason the ALB path was not missed.

> **AWS Budgets became a real brake when the architecture changed.** Its actions
> are limited to applying an IAM policy or SCP and stopping EC2/RDS. While this
> server authenticated upstream with Cognito, an IAM deny on the execution role
> would not have blocked a single retrieval, so a budget action was decorative.
> It now calls `bedrock:Retrieve` under its execution role, and a deny attached
> to that role stops every retrieval. Budgets still lag billing data by hours,
> so it is a backstop, not a trip wire — but it is no longer inert.

AWS WAF has nothing to attach to here — neither a Lambda function nor an
AgentCore Gateway is in WAF's protected-resource list — so reserved concurrency
is the brake at the infrastructure layer, and with an open gateway there is
nothing above it. There is no per-client control to reach for: `--pause` is
all-or-nothing. Switching the gateway to `CUSTOM_JWT` is what buys the
finer-grained one back — a runaway client becomes identifiable by its Cognito
client id, and deleting that app client is the per-client equivalent of
`--pause`. That is the trade being made; see
[Why the endpoint is open](#why-the-endpoint-is-open).

### Credentials

**This deployment ships no secrets.** It reads the knowledge base under its
Lambda execution role, granted `bedrock:Retrieve` on one knowledge base ARN and
nothing else by an inline policy that `deploy-lambda.sh` writes on every run.
The only environment variable is `KNOWLEDGE_BASE_ID`, which is not sensitive.

That is a change worth noting, because it removed a whole class of problem
rather than mitigating it. The server used to carry `CLIENT_ID` and
`CLIENT_SECRET` as Lambda environment variables, which meant the deploy script
had to keep secrets out of `argv` (a `0600` file in a `mktemp -d`, passed by
reference, deleted on an `EXIT` trap) and parse `.env` literally rather than
`source`ing it, and it still left the values readable to anyone with
`lambda:GetFunction`. None of that applies now.

The Cognito credentials still exist — they are what *clients* of the gateway
authenticate with — but they live only in a developer's gitignored `.env`, never
in the deployment. **!!! Never commit client secrets or access tokens !!!**

## Layout

```
config.py           Config.from_env() — env-var configuration
knowledge_base.py   KnowledgeBaseClient: Bedrock Retrieve + response normalization
public_source.py    PublicSourceResolver: document -> public download URL
passages.py         dedupe / project / intern_sources — pure shaping of passage lists
chart_parsing.py    pure parsing of embedded chart/table data blocks
semantics.py        basis, years_covered, truncation and estimate annotation
tools/
  base.py           BaseTool — self-describing tool contract
  search.py         SearchEnergyKnowledgeTool
  timeline.py       MetricTimelineTool
  chart_data.py     ChartDataTool
  __init__.py       build_tools()
tests/              unit tests for the pure modules
server.py           entrypoint: wires config → client → tools → MCPServer
lambda_handler.py   Lambda entrypoint: the AgentCore Gateway target contract
tool_schema.py      generates the gateway's static tool catalogue from the above
dev_run.py          local HTTP server
client_example.py   example MCP-SDK client against the local HTTP server
```

### Adding a tool

1. Subclass `BaseTool` in a new module under `tools/`.
2. Declare `name`, `title`, `description` as class attributes, and annotate every
   parameter of `run` with `Annotated[T, Field(description=...)]`. `register`
   feeds these straight into `tools/list`, so documentation cannot drift from the
   implementation.
3. Add it to `build_tools()`.
4. Add the module to the `COPY` line in the `Dockerfile` if you added one.
5. Ship it with **both** deploy scripts — `./infra/deploy-lambda.sh &&
   ./infra/create-gateway.sh`. The first makes the tool exist; the second is
   what makes the gateway advertise it.

Constraints expressed as `Literal[...]`, `ge=`, `le=` or a default are enforced
by pydantic at call time and, separately, rendered into the description that
reaches the gateway — AgentCore's schema dialect has no way to carry them. That
happens automatically in `tool_schema.py`; nothing needs writing twice.

Query the knowledge base via `self.search(...)` rather than the client directly —
it translates failures into `ToolError`, which is the only exception type whose
message the MCP SDK preserves for the caller. Any other exception reaches the
caller as a bare `"Error executing tool <name>"` with no reason, which a model
cannot act on.

Everything reaching a caller — tool `title`/`description`, `Field` descriptions,
server `instructions`, and exception messages — describes the service on its own
terms and stays free of implementation detail. Internal comments and module
docstrings stay technically accurate.

## Internals

<!-- Maintainer-facing. Nothing here is exposed through MCP. -->

`KnowledgeBaseClient` calls `bedrock-agent-runtime:Retrieve` directly with the
ambient AWS identity — an execution role on Lambda, a profile or keys locally —
and normalizes the result. It holds no credentials of its own.

It used to reach the knowledge base the long way round: mint a Cognito token,
call a `bfe-public-knowledge___Retrieve` tool on the AgentCore Gateway, and let
the gateway's `bedrock-knowledge-bases` connector forward to this same API. Once
the gateway moved in front of this server that path became a loop — client →
gateway → server → gateway → Bedrock — with two AgentCore hops per search. The
connector is a passthrough over exactly `managedSearchConfiguration`, so calling
the API directly loses nothing except `AgenticRetrieveStream`, which nothing
here used, and gains per-request `numberOfResults`, `filter`, reranker tuning
and `nextToken`.

Normalization flattens each hit into the `{score, text, source}` shape above,
sorts by score descending with an explicit tie-break on title, deduplicates,
truncates to `max_results`, then resolves public download URLs. There is no
`rank` field — list order carries it, and the pooling tools would otherwise
show a per-search rank in a flat combined list, where it reads as a global
ranking it is not. Deduplication happens *before* the truncation — the corpus files some
publications under more than one document name, so a query routinely retrieves
one passage twice, and deduplicating afterwards would return fewer results than
asked for.

The knowledge base is **managed**, which constrains the call: retrieval is
configured through `managedSearchConfiguration`, and the `vectorSearchConfiguration`
that most examples show is rejected with a `ValidationException`.

The S3 object URL that upstream returns is **not** publicly fetchable — it
403s without AWS credentials — so it is not exposed. `public_source.py` instead
matches each document against the public SFOE publication database by title and
date and returns a real, credential-free URL. Its module docstring explains the
scoring; the short version is that title similarity is scored bidirectionally
(one-directional coverage produced a false positive), publication-date agreement
is a bonus rather than a gate (a re-issued document legitimately changes date),
and PDFs are preferred over the companion spreadsheets that share a title and
date with their report. Lookups are cached in-process, including misses.
Resolution is best-effort and never raises: if it fails, the search still
returns its passages, just without links.

### Retrieval breadth

`RETRIEVAL_BREADTH` in `knowledge_base.py` is how many passages every `Retrieve`
asks for, regardless of how many the caller wants back. It is 50, and it is
deliberately **not** `max_results`: passages are deduplicated afterwards, and
the managed reranker applies its own relevance cutoff and returns fewer than
requested (50 asked → ~37 returned, 100 → ~63), so the number is an upper bound
rather than a promise. Asking for exactly `max_results` would hand back fewer
than requested as soon as two copies collapsed. Hard limit is 100.

Why 50 matters. The provided gateway pinned it to 5, Bedrock's default. Measured
over 12 DE/FR/IT/EN questions, five results came from a mean of 3.4 distinct
documents with 48% of them from a single document — one Italian question was
answered entirely out of one PDF. At 25 the same questions draw on 12.8 distinct
documents with a 25% top-document share.

This used to be a property of the gateway rather than of the question, and could
not be otherwise: the `bedrock-knowledge-bases` connector accepts **no**
`parameterOverrides` paths at all —

```text
ValidationException: Connector target validation failed: Configuration
'Retrieve': parameterOverride path '<anything>' is not a recognized override.
```

— verified against `/retrievalConfiguration` and its children,
`/knowledgeBaseId`, `/retrievalQuery`, `/maxResults`, `/searchType`, dotted and
`$`-prefixed notations, and `"*"`. Owning the `Retrieve` call is what dissolved
that constraint. Breadth *could* now scale with `max_results`; it deliberately
does not yet, because retrieval quality and the transport changed in the same
step and moving both at once would make a regression impossible to attribute.

Two consequences are baked into the code and should not be "tidied" without
re-measuring:

- `get_chart_data` issues several query variants and pools them. That is about
  *wording*, not breadth: the `<data>` blocks are transcribed in English while
  the prose is German, so a German-only probe set retrieves the passage around
  a figure and misses the chunk holding its numbers.
- `RESULTS_PER_PROBE` and `max_results` are local trims of what was already
  retrieved. Lowering them does not save a request; it discards results that
  were already fetched.

### Configuration

`KNOWLEDGE_BASE_ID`, `AWS_REGION`, `HOST` and `PORT` come from the environment,
all with defaults (`config.py`). AWS credentials come from wherever boto3 finds
them. There is nothing else — no token URL, no gateway URL, no client id, no
client secret.

The scripts at the repo root need `GATEWAY_URL` and nothing else, because the
gateway is open. The Cognito values that remain in `.env.example` are optional
and apply only if the gateway is switched back to `CUSTOM_JWT`; `gateway_client`
sends a bearer token when all three are set and omits the header when none are.
If they are used: several Cognito pools exist in the account and their
credentials are not interchangeable — a token minted by a pool the gateway does
not trust is issued normally and then rejected with 403, so a successful token
response is no evidence that `CLIENT_ID`/`CLIENT_SECRET` match `GATEWAY_URL`.
