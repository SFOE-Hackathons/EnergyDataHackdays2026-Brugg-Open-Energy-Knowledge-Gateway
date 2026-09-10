# Architecture

## Overview

This MCP server sits between a local LLM client (Claude Desktop, Claude Code) and two
external systems:

1. **AWS Bedrock AgentCore Gateway** — a managed MCP-over-HTTP endpoint, secured by
   Cognito OAuth2 client-credentials, wrapping a Bedrock Knowledge Base built from ~5GB of
   public SFOE PDFs. This is what `ask_energy_question` calls.
2. **pubdb.bfe.admin.ch** — the SFOE's public publication search/download site, which is
   where those PDFs originally came from. `verify_source` and `get_time_series` fetch the
   *original* PDF here directly, rather than trusting the Knowledge Base's own (sometimes
   OCR-noisy) extraction.

A few design decisions shape the code, and are worth knowing before reading it:

- **Tools return raw fetched material; the calling LLM judges correctness.** None of the
  three tools computes a "verified: true/false" verdict itself — `verify_source` and
  `get_time_series` return the actual extracted text of the matching page(s) and leave the
  comparison to the model. This avoids a brittle deterministic correctness-checker and
  matches how well LLMs already reconcile numbers when given real source text.
- **No AWS/infrastructure details ever reach tool output.** Citations show only a
  publication filename/title and a relevance score — never an S3 URI or `amazonaws.com`
  domain, even in error/fallback paths (`formatting.py` actively scrubs these).
- **Secrets vs. configuration are split.** `CLIENT_ID`/`CLIENT_SECRET` live in a gitignored
  `.env`. The Cognito/Gateway/pubdb URLs are not secrets, so they live in a checked-in
  `config.ini`, loaded by `src/energy_gateway_mcp/config.py`.
- **stdio only, today.** The server runs as a local subprocess (`mcp.run()` defaults to the
  `stdio` transport) — see the README for what would be needed to expose it remotely
  (e.g. for ChatGPT).

## Components

```mermaid
flowchart LR
    subgraph clients["MCP Clients"]
        CD["Claude Desktop"]
        CC["Claude Code"]
        GPT["ChatGPT<br/>(needs remote HTTP —<br/>not supported today)"]
    end

    subgraph local["Local MCP Server (stdio subprocess)"]
        SRV["server.py<br/>ask_energy_question / verify_source / get_time_series"]
        GWC["gateway_client.py<br/>OAuth token cache + Gateway calls"]
        PDC["pubdb_client.py<br/>search / download / page-match"]
        FMT["formatting.py<br/>citation rendering + AWS scrubbing"]
        CFG["config.py<br/>+ config.ini"]
    end

    subgraph aws["AWS (sandbox account)"]
        COG["Amazon Cognito<br/>OAuth2 token endpoint"]
        AGW["Bedrock AgentCore Gateway<br/>(bfe-public-knowledge___Retrieve)"]
        KB["Bedrock Knowledge Base<br/>KB-bfe-public"]
        S3[("S3: sandbox-bfe-public-data-pdf")]
    end

    PUBDB["pubdb.bfe.admin.ch<br/>(public search + PDF downloads)"]

    CD -.->|stdio| SRV
    CC -.->|stdio| SRV
    GPT -.->|"not connected"| SRV

    SRV --> GWC
    SRV --> PDC
    SRV --> FMT
    GWC --> CFG
    PDC --> CFG

    GWC -->|"1 . client_credentials"| COG
    GWC -->|"2 . tools/call Retrieve"| AGW
    AGW --> KB
    KB --> S3

    PDC -->|"search + download PDF"| PUBDB
```

| File | Responsibility |
|---|---|
| `server.py` | Defines the three MCP tools; catches every custom exception from the client modules and turns it into a friendly string — a single failed call must never crash the server process. |
| `gateway_client.py` | OAuth token fetch/cache (with one forced retry on `401`) and the JSON-RPC `tools/call` request to the AgentCore Gateway. |
| `pubdb_client.py` | Searches pubdb.bfe.admin.ch, disambiguates results, downloads PDFs, and ranks pages by keyword overlap (with EN/DE/FR/IT synonym expansion) against a claim. |
| `formatting.py` | Turns a raw Gateway response into cited answer text; flags chart/image-derived (OCR) passages; strips any AWS/S3 URL from output, including in fallback/error paths. |
| `config.py` / `config.ini` | Non-secret configuration (URLs) — loaded via stdlib `configparser`, resolved relative to the package so it works regardless of the caller's working directory. |

## Sequence: `ask_energy_question`

```mermaid
sequenceDiagram
    participant Client as MCP Client<br/>(Claude Desktop/Code)
    participant Server as server.py
    participant GWClient as gateway_client.py
    participant Cache as TokenCache
    participant Cognito as AWS Cognito
    participant Gateway as AgentCore Gateway
    participant KB as Bedrock Knowledge Base
    participant Fmt as formatting.py

    Client->>Server: tools/call ask_energy_question(question)
    Server->>GWClient: retrieve(question)
    GWClient->>Cache: get()
    alt token cached and not near expiry
        Cache-->>GWClient: cached access_token
    else missing or expired
        Cache->>Cognito: POST /oauth2/token (client_credentials)
        Cognito-->>Cache: access_token, expires_in
        Cache-->>GWClient: access_token
    end
    GWClient->>Gateway: POST /mcp tools/call Retrieve (Bearer token)
    Gateway->>KB: Retrieve(retrievalQuery)
    KB-->>Gateway: retrievalResults[]
    Gateway-->>GWClient: JSON-RPC result
    opt Gateway returned HTTP 401
        GWClient->>Cache: get(force_refresh=true)
        Cache->>Cognito: POST /oauth2/token
        Cognito-->>Cache: new access_token
        GWClient->>Gateway: retry tools/call Retrieve
        Gateway-->>GWClient: JSON-RPC result
    end
    GWClient-->>Server: parsed JSON-RPC response
    Server->>Fmt: format_answer(question, raw)
    Fmt-->>Server: passages + citations + anti-interpolation note
    Server-->>Client: tool result (text)
```

## Sequence: `verify_source` (and `get_time_series`, once per year)

```mermaid
sequenceDiagram
    participant Client as MCP Client
    participant Server as server.py
    participant PD as pubdb_client.py
    participant PubDB as pubdb.bfe.admin.ch

    Client->>Server: tools/call verify_source(document_title, claim)
    Server->>PD: verify_citation(document_title, claim)
    PD->>PD: parse_document_title() -> query, date
    PD->>PubDB: GET /de/suche?q=...&from=...&to=...
    PubDB-->>PD: search results (HTML)
    PD->>PD: disambiguate (exact date, then exact title,<br/>then unique non-"Vorabzug" edition)
    alt no confident match
        PD-->>Server: PubDbLookupError
        Server-->>Client: "could not confidently locate this source"
    else confident match
        PD->>PubDB: GET /xx/publication/download/<id>
        PubDB-->>PD: PDF bytes
        PD->>PD: extract_matching_pages():<br/>per-page pypdf text + keyword-overlap ranking
        PD-->>Server: best-matching page(s) text
        Server-->>Client: original-source text (LLM compares it itself)
    end
```

`get_time_series` runs this exact flow once per year in the requested range (searching
`"<topic> <year>"` each time), aggregating the results into one labeled report; a failure
for one year is caught and reported inline rather than aborting the whole call.
