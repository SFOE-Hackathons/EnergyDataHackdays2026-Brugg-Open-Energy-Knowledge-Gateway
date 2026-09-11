# SFOE Open Energy Knowledge Agent

A RAG-based AI agent for exploring official Swiss Federal Office of Energy (SFOE / BFE / OFEN) publications through the **SFOE Open Energy Knowledge Gateway**.

The application automatically decides which SFOE MCP tool is most appropriate for a user question, retrieves official evidence, and uses a benchmark-selected LLM to generate grounded answers with source citations.

> Built for the Energy Data Hackdays 2026 challenge: **Open Energy Knowledge Gateway**

---

## 1. Project Goal

Official energy information is distributed across many reports, publications, charts, and statistical documents.

A normal user should not need to know:

- which document contains the answer,
- which retrieval method to use,
- which MCP tool is appropriate,
- how to formulate tool arguments,
- or how to interpret retrieved evidence.

This project provides a simple natural-language interface on top of the SFOE Open Energy Knowledge Gateway.

Example:

```text
How has photovoltaic production developed from 2020 to 2024?
```

The agent then performs:

```text
User question
      ↓
LLM-based tool router
      ↓
Select appropriate SFOE MCP tool
      ↓
Retrieve official SFOE evidence
      ↓
Generate grounded answer with citations
      ↓
Show answer + official sources
```

---

## 2. What We Built

This repository adds an AI-agent layer on top of the SFOE Open Energy Knowledge Gateway.

### Provided by SFOE / challenge infrastructure

The challenge infrastructure provides:

- official SFOE publications and data,
- the knowledge retrieval backend,
- the MCP gateway,
- the MCP tool definitions,
- structured source attribution.

### Developed in this project

We developed:

- a baseline RAG application,
- dynamic MCP tool discovery,
- an LLM-based tool router,
- tool argument generation,
- automatic tool-call repair,
- evidence normalization,
- source mapping and deduplication,
- grounded answer generation,
- claim-level citation handling,
- model benchmarking,
- semantic LLM evaluation,
- agent routing evaluation,
- end-to-end agent evaluation,
- a Streamlit demo UI,
- a Windows batch launcher.

We did **not** create or train:

- the foundation LLMs,
- the SFOE publications,
- the SFOE knowledge base,
- the underlying embeddings / vector index,
- or the SFOE MCP tools.

---

## 3. Architecture

```text
                         ┌─────────────────────────┐
                         │        User             │
                         │  Natural-language query │
                         └────────────┬────────────┘
                                      │
                                      ▼
                         ┌─────────────────────────┐
                         │   Streamlit Frontend    │
                         │   streamlit_app.py      │
                         └────────────┬────────────┘
                                      │
                                      ▼
                         ┌─────────────────────────┐
                         │      AI Agent           │
                         │      agent_app.py       │
                         │                         │
                         │  1. Tool discovery      │
                         │  2. Tool routing        │
                         │  3. Argument creation   │
                         └────────────┬────────────┘
                                      │
                                      ▼
                   ┌───────────────────────────────────┐
                   │     SFOE MCP / AgentCore Gateway  │
                   └──────────────┬────────────────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              │                   │                   │
              ▼                   ▼                   ▼
   ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
   │ Knowledge Search │ │ Metric Timeline  │ │ Chart / Table    │
   │                  │ │                  │ │ Data Extraction  │
   └────────┬─────────┘ └────────┬─────────┘ └────────┬─────────┘
            │                    │                    │
            └────────────────────┼────────────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │ Official SFOE evidence  │
                    │ + source metadata       │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │ NVIDIA Nemotron         │
                    │ via Amazon Bedrock      │
                    │ Grounded answer         │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │ Answer + citations +    │
                    │ official SFOE sources   │
                    └─────────────────────────┘
```

---

## 4. MCP Tools

The agent discovers the available MCP tools dynamically from the gateway.

At the time of development, the gateway exposed three main tools.

### `bfe-energy___search_energy_knowledge`

Used for qualitative or general knowledge questions.

Example:

```text
What role does hydropower play in Switzerland?
```

Typical output includes:

- relevant official passages,
- relevance scores,
- source IDs,
- publication titles,
- publication dates,
- official download links.

### `bfe-energy___get_metric_timeline`

Used for annual time-series or trend questions.

Example:

```text
How has photovoltaic production developed from 2020 to 2024?
```

For the Streamlit UI, the application requests:

```text
detail="full"
```

for timeline questions so the final LLM receives the numerical evidence rather than only index metadata.

### `bfe-energy___get_chart_data`

Used when the user explicitly asks for values from charts or tables.

Example:

```text
What values are shown in the SFOE chart for renewable electricity production?
```

The agent preserves important source semantics such as:

- exact vs estimated values,
- extraction method,
- projections,
- data bases,
- covered years,
- truncation flags.

Estimated values are not presented as exact measurements.

---

## 5. LLM Selection

Instead of selecting a model manually, several Bedrock-accessible models were benchmarked.

Candidate models included:

- NVIDIA Nemotron 3 Super 120B
- Qwen3 235B
- Qwen3 32B
- Qwen3-Coder 30B
- GLM 4.7 Flash
- Devstral 2 123B

The final semantic comparison used an independent LLM judge and evaluated dimensions such as:

- groundedness,
- citation correctness,
- completeness,
- numerical accuracy,
- abstention,
- relevance,
- language quality,
- latency.

### Selected production model

```text
nvidia.nemotron-super-3-120b
```

NVIDIA Nemotron achieved the strongest overall semantic result among the finalists and was therefore selected as the production model.

### Independent judge model

```text
qwen.qwen3-235b-a22b-2507-v1:0
```

The production model and judge model are intentionally different.

---

## 6. Agent Workflow

For every user question, the system follows the same high-level workflow.

```text
1. Receive question
2. Discover / load live MCP tools
3. Ask Nemotron to choose exactly one tool
4. Generate arguments from the live tool schema
5. Call the selected MCP tool
6. If needed, perform one controlled argument-repair attempt
7. Normalize evidence and source metadata
8. Build grounded context
9. Generate final answer with Nemotron
10. Clean / deduplicate citations
11. Display only actually cited sources
```

The agent does not hard-code a single retrieval strategy. It dynamically chooses the tool based on the question.

---

## 7. Evaluation

The project evaluates different parts of the system separately rather than relying on one overall score.

### 7.1 Model evaluation

Purpose:

```text
Which LLM should power the application?
```

Several Bedrock models were benchmarked on grounded RAG answer quality.

The final semantic evaluation selected **NVIDIA Nemotron 3 Super**.

### 7.2 Routing benchmark

Purpose:

```text
Does the agent choose the correct MCP tool?
```

The routing benchmark included:

- Search questions,
- Timeline questions,
- Chart / table questions,
- English,
- German,
- French.

Result:

```text
Questions:                21
Routing accuracy:         100%
Argument schema validity: 100%
Initial MCP success:      100%
Final MCP success:        100%
Agent route success:      100%
```

No automatic repair was needed in the 21 routing benchmark cases.

### 7.3 End-to-end benchmark

Purpose:

```text
Does the complete deployed system produce a useful,
grounded, correctly cited final answer?
```

The corrected benchmark evaluates:

- routing correctness,
- MCP execution,
- groundedness,
- citation correctness,
- completeness,
- numerical accuracy,
- uncertainty handling,
- abstention quality,
- relevance,
- language quality,
- deployed-agent latency.

The independent judge is **not** included in deployed-agent latency.

#### Overall corrected result

```text
Questions:               15
End-to-end score:        0.912
Semantic quality:        0.905
Routing accuracy:        100%
MCP success:             100%
Pipeline success:        100%

Groundedness:            4.40 / 5
Citation correctness:    4.47 / 5
Completeness:            4.67 / 5
Numerical accuracy:      4.40 / 5
Uncertainty handling:    4.07 / 5
Abstention quality:      4.93 / 5

Average agent latency:   13.77 s
```

One large latency outlier significantly affected the mean. Typical responses were much faster.

#### By tool family

```text
Search semantic quality:   0.896
Timeline semantic quality: 0.882
Chart semantic quality:    0.938
```

---

## 8. Repository Structure

A typical repository layout is:

```text
.
├── agent_app.py
├── app.py
├── streamlit_app.py
├── test_gateway.py
├── benchmark_rag_models.py
├── evaluate_rag_semantic.py
├── benchmark_agent_routing.py
├── benchmark_agent_end_to_end.py
├── requirements.txt
├── start_sfoe_agent.bat
├── .env.example
├── .gitignore
├── README.md
│
└── benchmark_results/
    ├── agent_routing_details.json
    ├── agent_routing_scores.csv
    ├── agent_routing_summary.csv
    ├── agent_end_to_end_details.json
    ├── agent_end_to_end_scores.csv
    └── agent_end_to_end_summary.csv
```

---

## 9. Installation

### Requirements

Recommended:

```text
Python 3.11+
```

Install dependencies with:

```bash
pip install -r requirements.txt
```

Main dependencies:

```text
boto3
requests
python-dotenv
streamlit
```

---

## 10. Environment Configuration

The real `.env` file contains local credentials and must **never** be committed to GitHub.

The repository contains:

```text
.env.example
```

Copy it.

### Windows

```powershell
copy .env.example .env
```

### macOS / Linux

```bash
cp .env.example .env
```

Then edit `.env` and provide your own credentials.

Example structure:

```env
TOKEN_URL="https://<your-cognito-domain>/oauth2/token"

GATEWAY_URL="https://<your-gateway>/mcp"

AWS_REGION="eu-central-1"

AWS_ACCESS_KEY_ID="YOUR_AWS_ACCESS_KEY_ID"
AWS_SECRET_ACCESS_KEY="YOUR_AWS_SECRET_ACCESS_KEY"

BEDROCK_MODEL_ID="nvidia.nemotron-super-3-120b"

TOP_K_RESULTS=5

JUDGE_MODEL_ID="qwen.qwen3-235b-a22b-2507-v1:0"
MAX_JUDGE_TOKENS=3000
```

If Cognito client credentials are required in your environment:

```env
CLIENT_ID="YOUR_COGNITO_CLIENT_ID"
CLIENT_SECRET="YOUR_COGNITO_CLIENT_SECRET"
```

### Important security note

Never commit:

```text
.env
AWS access keys
AWS secret keys
Cognito client secrets
access tokens
```

The `.gitignore` should contain:

```gitignore
.env
```

The `.env.example` file is safe to commit because it contains only placeholders.

---

## 11. AWS Requirements

A developer running the application locally needs AWS permissions compatible with the resources used by the project, including access to the selected Amazon Bedrock model.

The application currently uses:

```text
Region:
eu-central-1
```

Production model:

```text
nvidia.nemotron-super-3-120b
```

A user who does not have appropriate AWS permissions cannot simply clone the repository and use the backend with placeholder credentials.

For non-technical end users, a hosted deployment is therefore preferable.

---

## 12. Running the Gateway Test

Before starting the UI, verify that the MCP connection works:

```powershell
python .	est_gateway.py
```

A successful test should:

- authenticate if Cognito is configured,
- connect to the MCP gateway,
- discover the available MCP tools.

If multiple Python installations exist on Windows, use the same interpreter that contains the installed dependencies.

Example:

```powershell
C:\Path\To\Python\python.exe .	est_gateway.py
```

---

## 13. Running the Streamlit UI

Start the web application with:

```powershell
python -m streamlit run .\streamlit_app.py
```

Do **not** run:

```powershell
python streamlit_app.py
```

Streamlit applications must be launched with `streamlit run`.

After startup, the browser normally opens automatically.

Typical local address:

```text
http://localhost:8501
```

---

## 14. Windows Double-Click Launcher

For less technical Windows users, the repository includes:

```text
start_sfoe_agent.bat
```

Place it in the same folder as:

```text
streamlit_app.py
agent_app.py
requirements.txt
.env
```

Then double-click:

```text
start_sfoe_agent.bat
```

The launcher:

- checks for Python,
- checks the required project files,
- installs dependencies,
- starts Streamlit,
- opens the application in the browser.

The `.bat` file does **not** contain credentials.

---

## 15. Using the Application

The Streamlit interface provides:

- natural-language question input,
- example questions,
- automatically selected MCP tool,
- grounded answer,
- official SFOE sources,
- source links,
- agent latency,
- optional agent details.

Example questions:

### General knowledge

```text
What role does hydropower play in Switzerland?
```

Expected tool:

```text
Knowledge Search
```

### Timeline

```text
How has photovoltaic production developed from 2020 to 2024?
```

Expected tool:

```text
Metric Timeline
```

### Chart / table

```text
What values are shown in the SFOE chart for renewable electricity production?
```

Expected tool:

```text
Chart / Table Data
```

The application can also answer questions in multiple languages, including:

- English,
- German,
- French.

---

## 16. Grounding and Citation Rules

The final answer prompt instructs the LLM to:

- answer only from retrieved evidence,
- cite factual claims,
- avoid unsupported claims,
- distinguish production from demand or capacity,
- preserve projection semantics,
- preserve estimate semantics,
- not invent page numbers,
- not invent missing numerical values,
- abstain when evidence is insufficient.

For chart data:

```text
estimated value ≠ exact value
```

The application therefore avoids turning estimated ranges into artificially precise numbers.

---

## 17. Source Handling

The SFOE tools return source metadata separately from evidence records.

The application:

1. resolves source IDs,
2. normalizes metadata,
3. deduplicates sources,
4. preferentially deduplicates using official download URLs,
5. maps duplicate sources to one citation number,
6. removes unused sources,
7. renumbers displayed citations consecutively.

The UI displays only sources actually cited by the final answer.

---

## 18. Error Handling

The Streamlit demo intentionally hides raw Python tracebacks from end users.

Instead of exposing implementation details, users receive messages such as:

```text
The SFOE knowledge service could not complete this request.
Please try again or make the question more specific.
```

This keeps the demo clean while development tools such as `test_gateway.py` remain available for technical debugging.

---

## 19. Known Limitations

The current prototype performs strongly overall, but some limitations remain.

### Ambiguous definitions

Some questions may involve source terminology that is not fully consistent across publications.

Example:

```text
Does a percentage refer to production, consumption,
demand, capacity, or another base?
```

Future versions should provide even stronger cross-source ambiguity handling.

### Broad timeline metrics

Broad concepts such as:

```text
renewable electricity production
```

may refer to several possible definitions or data bases.

More explicit metric disambiguation could improve robustness.

### External service dependency

The application depends on:

- SFOE MCP gateway availability,
- network connectivity,
- Amazon Bedrock availability,
- AWS permissions.

### Latency

Tool retrieval and LLM generation can sometimes introduce noticeable latency.

Most routing decisions are fast; retrieval and external service response time are the main contributors.

### LLM-as-a-judge

Semantic benchmark results use an independent LLM judge.

This is useful for scalable evaluation but should not be interpreted as a perfect substitute for human expert review.

---

## 20. Future Improvements

Possible next steps include:

- hosted public deployment,
- improved metric disambiguation,
- stronger handling of contradictory publications,
- ambiguous-routing stress tests,
- conversational memory,
- comparison of several retrieved metrics,
- automatic visualization of time-series results,
- richer chart rendering,
- human expert evaluation,
- latency optimization,
- more multilingual benchmark questions,
- direct feedback collection from users.

---

## 21. Benchmark Philosophy

The evaluation was intentionally separated into multiple layers.

```text
Model benchmark
→ Which LLM is best?

RAG evaluation
→ Does retrieved evidence lead to grounded answers?

Routing benchmark
→ Does the agent choose the correct tool?

MCP execution test
→ Can the tool actually be called successfully?

End-to-end benchmark
→ Does the complete application produce a useful final answer?
```

This makes failures easier to diagnose and avoids hiding different problems inside a single aggregate score.

---

## 22. Demo Story

A concise presentation flow is:

```text
1. SFOE already provides high-quality official energy knowledge.

2. SFOE exposes specialized retrieval capabilities through MCP.

3. A normal user should not need to know which tool to call.

4. We built an AI agent that automatically selects the right tool.

5. The agent retrieves official evidence and generates cited answers.

6. We benchmarked several LLMs and selected NVIDIA Nemotron.

7. We separately evaluated routing and full end-to-end performance.

8. The result is a multilingual, grounded SFOE energy assistant.
```

Key evaluation numbers:

```text
Routing benchmark:
100% routing accuracy
100% MCP success

End-to-end benchmark:
0.912 overall score
0.905 semantic quality
100% routing accuracy
100% MCP success
100% pipeline success
```

---

## 23. Challenge Links

Energy Data Hackdays:

https://www.energydatahackdays.ch/

Challenge:

https://www.energydatahackdays.ch/challenges/open-energy-knowledge-gateway

Repository:

https://github.com/SFOE-Hackathons/EnergyDataHackdays2026-Brugg-Open-Energy-Knowledge-Gateway

---

## 24. Technology Stack

```text
Python
Streamlit
Amazon Bedrock
NVIDIA Nemotron
Qwen
AWS Cognito
AWS Bedrock AgentCore Gateway
Model Context Protocol (MCP)
SFOE Open Energy Knowledge Gateway
```

---

## 25. Summary

This project demonstrates how an LLM-based agent can sit on top of specialized official-data tools and make them accessible through one natural-language interface.

The central idea is not merely:

```text
Ask an LLM a question
```

but:

```text
Understand the question
        ↓
Select the appropriate trusted tool
        ↓
Retrieve official evidence
        ↓
Generate a grounded answer
        ↓
Show the original sources
```

The result is a practical prototype for making official Swiss energy knowledge easier to access while preserving grounding, traceability, and source transparency.
