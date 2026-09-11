"""Runtime configuration for the energy knowledge MCP server.

Loaded from environment variables, all of which have working defaults except
the AWS credentials themselves, which come from the ambient credential chain
(an execution role on Lambda, a profile or key pair locally).

This server holds NO secrets. It reaches the knowledge base with
`bedrock-agent-runtime:Retrieve` under whatever identity it is running as, so
authorization is an IAM grant on that identity rather than a credential shipped
alongside the code. Inbound authentication is the AgentCore Gateway's job, not
this server's -- see infra/create-gateway.sh.

That was not always true: an earlier revision exchanged Cognito
client_credentials for a bearer token and called a `Retrieve` tool through the
gateway. Removing that hop halved the latency, deleted the client secret from
the deployment, and -- because `numberOfResults` is a plain request field on the
Retrieve API but was frozen into the gateway's connector target -- turned
retrieval breadth back into something the caller can influence. See
knowledge_base.py.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    knowledge_base_id: str
    region: str
    host: str
    port: int

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            # KB-bfe-public: a MANAGED Bedrock knowledge base, which is why
            # retrieval is configured through `managedSearchConfiguration` and
            # not `vectorSearchConfiguration` -- the latter is rejected outright
            # for managed knowledge bases.
            knowledge_base_id=os.environ.get("KNOWLEDGE_BASE_ID", "ZPVWAEHXNB"),
            # Falls back to the standard AWS variables so that a normally
            # configured shell or Lambda needs no extra setting, and only then
            # to the region the knowledge base actually lives in.
            region=os.environ.get(
                "AWS_REGION",
                os.environ.get("AWS_DEFAULT_REGION", "eu-central-1"),
            ),
            host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "8000")),
        )
