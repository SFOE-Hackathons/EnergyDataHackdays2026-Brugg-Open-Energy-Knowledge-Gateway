# Open Energy Knowledge Gateway
### Challenge: 
![Slide 1](slides/Folie1.PNG)
![Slide 2](slides/Folie2.PNG)
![Slide 3](slides/Folie3.PNG)
![Slide 4](slides/Folie4.PNG)

# Weitere Infos
Login-URL fuer die Konsole:
https://542202863496.signin.aws.amazon.com/console


# What This Repository Contains

Everything below this section is the challenge brief as handed out, describing
the infrastructure that already existed. What the team built on top of it:

| Path | What it is |
| --- | --- |
| [`mcp-gateway/`](mcp-gateway/README.md) | The MCP server. It calls Bedrock `Retrieve` directly and turns the raw retrieval response into ranked passages with source attribution, public PDF download URLs, and per-passage annotations (what the numbers measure, which years they cover, whether they are projections or were cut off mid-chunk). It exposes three tools: `search_energy_knowledge`, `get_metric_timeline` and `get_chart_data`. |
| [`infra/deploy-lambda.sh`](infra/deploy-lambda.sh) | Deploys that server as a Lambda function that only the gateway's IAM role may invoke, with a hard concurrency cap and a one-command kill switch. It has no URL and no HTTP route of any kind. |
| [`infra/create-gateway.sh`](infra/create-gateway.sh) | The AgentCore Gateway clients actually talk to, as a single re-runnable script. It is not the `sandbox-bfe-public-kb` gateway described below. |
| `gateway_client.py`, `list_tools.py`, `query_gateway.py`, `test_gateway.py` | A dependency-light reference client and three scripts that exercise the deployed gateway end to end. |

## The shape of it

```text
MCP client ──(Cognito JWT)──▶ AgentCore Gateway ──(Lambda Invoke)──▶ MCP server
                                                                     Lambda
                                                                        │
                                                              (execution role, IAM)
                                                                        ▼
                                                         Bedrock KB-bfe-public
```

The gateway sits **in front of** the MCP server. That is worth stating because
it was the other way round for most of the build: the server held Cognito
credentials and called a `bedrock-knowledge-bases` connector target on the
gateway, which forwarded to Bedrock. Turning it around removed a full
round trip per search, deleted the client secret from the deployment, and made
retrieval parameters a property of the question rather than of the gateway.

Three consequences follow, and all three are deliberate:

- **The endpoint is authenticated, not open.** Reaching it needs a Cognito
  `client_credentials` token. An earlier design served it anonymously through
  CloudFront; that has been torn down. Access control is now the gateway's job,
  which is where it belongs if this ever serves more than one consumer.
- **The `bfe-public-knowledge___Retrieve` tool is gone.** Clients call
  `bfe-energy___search_energy_knowledge` and its two siblings instead. They
  return the same corpus, deduplicated, source-attributed and annotated.
- **The gateway invokes the Lambda rather than speaking MCP to it.** The
  natural design — an `mcp.mcpServer` target pointed at an IAM-authorised
  Function URL — cannot work: AgentCore's outbound SigV4 signer signs POST
  bodies as empty, and MCP's HTTP transport is POST-only, so every request is
  rejected on signature before the function runs. A Lambda target sidesteps the
  signer entirely. The price is that the tool catalogue is no longer
  discovered, so `create-gateway.sh` generates it from the server and uploads
  it — meaning a tool change needs **both** scripts re-run, in order. See
  [`mcp-gateway/lambda_handler.py`](mcp-gateway/lambda_handler.py) for the
  evidence behind that.

**On retrieval breadth.** The provided gateways pin `numberOfResults` to 5 —
Bedrock's default. Measured over 12 German, French, Italian and English
questions, those 5 passages came from a mean of 3.4 distinct documents, with 48%
of them from a single document; one Italian question was answered entirely out
of one PDF. On a connector target that value cannot be overridden per request:
the `bedrock-knowledge-bases` connector accepts no `parameterOverrides` paths at
all, so retrieval breadth is a property of the gateway and not of the question.
Owning the `Retrieve` call is what fixes that; it is set to 50 in
[`mcp-gateway/knowledge_base.py`](mcp-gateway/knowledge_base.py), which explains
why 50 and not 25.

**On the account's guardrails.** They shaped the deployment and are worth
knowing before extending it. AWS Organization `o-3r6tcb49g2` denies API Gateway,
ALB, App Runner, Lightsail and Amplify outright, and denies anonymous Lambda
invocation: an open Function URL (`--auth-type NONE`, `Principal: "*"`) returned
403 for every request with no log group ever created, while a direct
`lambda invoke` of the same function succeeded. See `mcp-gateway/README.md` for
the evidence. The deployment has no Function URL at all now, for the unrelated
reason above.


# Starting Point

The basic AWS infrastructure is already available so that the team can focus on the actual challenge rather than infrastructure setup.

### Data

Around **5 GB of public SFOE PDF documents from 2020 onwards** are available in:

```text
S3 bucket:
sandbox-bfe-public-data-pdf
```

The collection contains public SFOE reports, studies and publications.

Only public information is included in the Hackathon knowledge base.

### Amazon Bedrock Knowledge Base

A managed Amazon Bedrock Knowledge Base has already been created:

```text
KB-bfe-public
```

The knowledge base is connected to the S3 document collection and has been successfully tested with both standard and agentic retrieval.

Example question:

```text
Welche Rolle spielt Wasserkraft in der Schweizer Stromversorgung?
```

The Knowledge Base successfully retrieves relevant passages from SFOE publications together with document information, metadata and relevance scores.

### AgentCore Gateway

An Amazon Bedrock AgentCore Gateway is available:

```text
Name:
sandbox-bfe-public-kb
```

Gateway URL:

```text
https://sandbox-bfe-public-kb-8thmswsvit.gateway.bedrock-agentcore.eu-central-1.amazonaws.com/mcp
```

Target:

```text
bfe-public-knowledge
```

The Managed Knowledge Base is exposed through the Gateway as an MCP tool:

```text
bfe-public-knowledge___Retrieve
```

### Authentication

Inbound access to the Gateway is protected using:

```text
Amazon Cognito
OAuth 2.0
JWT
client_credentials flow
```

Client credentials will be provided separately during the Hackdays or can be found in Cognito.

**!!! Never commit Client Secrets or access tokens to GitHub !!!.**

---

# What Has Already Been Validated

The following end-to-end flow has been successfully tested:

```text
MCP Client
    ↓
Cognito access token
    ↓
AgentCore Gateway
    ↓
MCP tools/list
    ↓
MCP tools/call
    ↓
Bedrock Knowledge Base
    ↓
SFOE documents
    ↓
Relevant passages + sources + metadata
```

Successful tests include:

* obtaining an OAuth access token
* connecting to the AgentCore Gateway
* calling `tools/list`
* discovering the Knowledge Base MCP tool
* calling `bfe-public-knowledge___Retrieve`
* retrieving real SFOE knowledge through MCP

A reference Python script can be provided as a starting point.

---

# What We Want to Achieve During the Hackdays

The infrastructure proves that the basic concept works.

The challenge now is to determine:

> **What should a useful, trustworthy and reusable public Energy Knowledge Gateway actually look like?**

The Hackathon team should explore both the technical implementation and the broader concept.

---

# Suggested Next Steps

## 1. Connect a Real MCP Client

Move beyond the technical Python test and connect one or more real MCP-compatible applications.

Possible examples:

* an AI agent
* a custom chatbot
* MCP Inspector
* developer tools supporting remote MCP
* a custom Python or TypeScript application

### Minimum success

```text
AI application
      ↓ MCP
Open Energy Knowledge Gateway
      ↓
SFOE Knowledge Base
      ↓
Useful result
```

---

## 2. Build an AI Agent on Top

The Gateway currently provides retrieved knowledge.

Explore how an AI agent can:

1. understand the user's question
2. discover the available MCP tools
3. query SFOE knowledge
4. reason over the retrieved information
5. formulate a useful answer
6. show the underlying sources

Example:

```text
User:
"What role does hydropower play in Switzerland?"

       ↓

AI Agent
       ↓
MCP Gateway
       ↓
SFOE Knowledge
       ↓

Answer
+
SFOE sources
```

---

## 3. Improve Source Transparency

The current retrieval results already provide information such as:

* document ID
* document title
* S3 source
* retrieved passage
* relevance score
* metadata

Explore how this information should be presented to an end user.

Questions to investigate:

* How should citations be displayed?
* Should users be able to open the original SFOE publication?
* What metadata should every knowledge result provide?
* How can users distinguish authoritative sources from generated answers?

---

## 4. Improve Metadata

The current Knowledge Base contains automatically generated metadata.

Possible improvements include:

```text
title
publication_date
language
document_type
topic
source_url
publisher
```

An important goal could be to link retrieved knowledge back to the **original public SFOE webpage**, rather than only the S3 object.

---

## 5. Evaluate Retrieval Quality

Create a small evaluation set of representative Swiss energy questions.

Examples:

```text
What role does hydropower play in Switzerland?

What are Switzerland's renewable electricity targets?

How has photovoltaic production developed in Switzerland?

Welche Rolle spielt Wasserstoff in der Schweizer Energiepolitik?

Quels sont les objectifs suisses en matière d'énergie renouvelable?
```

Evaluate:

| Criterion      | Question                                                 |
| -------------- | -------------------------------------------------------- |
| Relevance      | Did we retrieve the right information?                   |
| Source quality | Is the information from an appropriate SFOE source?      |
| Completeness   | Is important context missing?                            |
| Language       | Does multilingual retrieval work?                        |
| Transparency   | Can the user understand where the information came from? |

---

## 6. Test Interoperability

A central hypothesis of this challenge is:

> **One knowledge gateway can serve multiple independent AI applications.**

A particularly strong Hackathon result would therefore connect **two different clients or agents** to the same MCP Gateway.

For example:

```text
AI Agent A ─────┐
                │
AI Agent B ─────┼── MCP Gateway ── SFOE Knowledge
                │
Custom App ─────┘
```

If this works, we demonstrate that organisations do not necessarily need to build separate RAG infrastructures around the same public knowledge.

---

## 7. Explore Governance and Operating Model

Technology is only one part of the challenge.

Discuss questions such as:

* Who maintains the knowledge?
* How often should the knowledge base be updated?
* What content should be exposed?
* How should access be controlled?
* Should the service be completely public?
* How should usage and cost be limited?
* How can users know when a source was last updated?
* What service level would an AI application expect?
* What responsibilities remain with the consuming AI application?

Document your recommendations.

---

# Suggested Team Organisation

The work can be split into parallel workstreams.

## Workstream A — MCP & Gateway

**Focus:** technical MCP integration

Tasks:

* understand the existing AgentCore Gateway
* inspect available MCP tools
* test `tools/list` and `tools/call`
* connect external MCP clients
* investigate AgentCore Gateway capabilities
* explore authentication and access control
* document technical integration

---

## Workstream B — AI Agents & Applications

**Focus:** demonstrate how applications can consume the Gateway

Tasks:

* connect an AI agent to the MCP Gateway
* build a simple user-facing use case
* test tool discovery
* generate answers from retrieved knowledge
* present citations and sources
* ideally connect more than one independent client

---

## Workstream C — Knowledge & Retrieval Quality

**Focus:** make the knowledge useful and trustworthy

Tasks:

* test representative energy questions
* evaluate retrieval quality
* identify duplicate or poor retrieval results
* investigate metadata
* improve source transparency
* test multilingual retrieval
* propose improvements to the Knowledge Base

---

## Workstream D — Concept, Governance & Vision

**Focus:** determine how such a service could work beyond the PoC

Tasks:

* define the target users
* identify valuable use cases
* define minimum metadata requirements
* discuss access and governance
* consider costs and scalability
* identify risks and limitations
* develop the future architecture
* prepare the final story and presentation
---


# Expected Hackathon Outcome

At the end of the Hackdays we would ideally have:

* a working end-to-end prototype
* at least one real AI application connected through MCP
* ideally multiple independent MCP clients
* transparent access to SFOE sources
* a documented architecture
* documented findings and limitations
* recommendations for improving the Knowledge Gateway
* a clear vision for how such a service could be operated and scaled

The goal is **not** to build a production-ready service in two days.

The goal is to demonstrate what is possible and learn what would be required to make trusted public energy knowledge reusable by an ecosystem of AI applications.

---

# After the Hackathon

If the concept proves valuable, the SFOE can evaluate how the findings could be transferred into its existing cloud and AI environment.

Potential next steps include:

```text
Hackathon PoC
      ↓
Architecture assessment
      ↓
Security & governance
      ↓
Integration with existing SFOE AI infrastructure
      ↓
Pilot
      ↓
Potential scalable Energy Knowledge Gateway
```

The Hackathon results should therefore be **reproducible, documented and reusable**.

