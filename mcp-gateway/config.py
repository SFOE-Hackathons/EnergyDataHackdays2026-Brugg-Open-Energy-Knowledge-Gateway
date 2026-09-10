"""Runtime configuration for the energy knowledge MCP server.

Loaded from environment variables. CLIENT_ID/CLIENT_SECRET are Cognito
client_credentials issued for the upstream Bedrock AgentCore Gateway;
TOKEN_URL/GATEWAY_URL point at that Cognito token endpoint and the
AgentCore Gateway MCP endpoint respectively.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    token_url: str
    gateway_url: str
    client_id: str
    client_secret: str
    host: str
    port: int

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            token_url=os.environ.get(
                "TOKEN_URL",
                "https://my-domain-ajdb98m7.auth.eu-central-1.amazoncognito.com/oauth2/token",
            ),
            gateway_url=os.environ.get(
                "GATEWAY_URL",
                "https://sandbox-bfe-public-kb-8thmswsvit."
                "gateway.bedrock-agentcore.eu-central-1.amazonaws.com/mcp",
            ),
            client_id=os.environ["CLIENT_ID"],
            client_secret=os.environ["CLIENT_SECRET"],
            host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "8000")),
        )
