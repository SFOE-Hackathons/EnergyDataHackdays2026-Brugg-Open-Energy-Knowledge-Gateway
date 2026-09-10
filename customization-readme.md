## Local Bedrock MCP server

`mcp-server.py` exposes an `ask_question` MCP tool backed by the Bedrock
Knowledge Base named `KB-bfe-public`. AWS credentials are read from the normal
AWS credential chain. The IAM identity needs permission to retrieve from the
Knowledge Base and to list Knowledge Bases when `KNOWLEDGE_BASE_ID` is not set.

Install the server dependencies:

```bash
./bin/pip install boto3 flashrank
```

The server retrieves six candidates from Bedrock, reranks them locally with
FlashRank, and returns only the requested `top_k` one or two chunks. This keeps
the context sent to a local Ollama model small.

Run over stdio:

```bash
AWS_REGION=eu-central-1 ./bin/python mcp-server.py
```

Run as a Streamable HTTP MCP server:

```bash
MCP_TRANSPORT=streamable-http PORT=8000 ./bin/python mcp-server.py
```

Set `KNOWLEDGE_BASE_ID` to the actual Bedrock Knowledge Base ID to skip name
resolution. `KNOWLEDGE_BASE_NAME` can be used when the display name differs
from `KB-bfe-public`.