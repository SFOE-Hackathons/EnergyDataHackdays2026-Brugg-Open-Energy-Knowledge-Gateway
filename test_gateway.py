import os
import json
import requests

CLIENT_ID = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]

TOKEN_URL = (
    "https://my-domain-ajdb98m7.auth.eu-central-1.amazoncognito.com/"
    "oauth2/token"
)

GATEWAY_URL = (
    "https://sandbox-bfe-public-kb-8thmswsvit."
    "gateway.bedrock-agentcore.eu-central-1.amazonaws.com/mcp"
)

MCP_PROTOCOL_VERSION = "2026-07-28"

TOOL_NAME = "bfe-public-knowledge___Retrieve"


def fetch_access_token():
    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        },
        timeout=30,
    )

    response.raise_for_status()
    return response.json()["access_token"]


def retrieve(gateway_url, access_token, question):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": TOOL_NAME,
    }

    payload = {
        "jsonrpc": "2.0",
        "id": "retrieve-request",
        "method": "tools/call",
        "params": {
            "name": TOOL_NAME,
            "arguments": {
                "retrievalQuery": {
                    "text": question
                }
            },
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientInfo": {
                    "name": "bfe-hackathon-test",
                    "version": "1.0.0"
                },
                "io.modelcontextprotocol/clientCapabilities": {}
            }
        }
    }

    response = requests.post(
        gateway_url,
        headers=headers,
        json=payload,
        timeout=120,
    )

    print("HTTP status:", response.status_code)
    print("\nRaw response:")
    print(response.text)

    response.raise_for_status()
    return response.json()


def main():
    question = (
        "Welche Rolle spielt Wasserkraft "
        "in der Schweizer Stromversorgung?"
    )

    print("Fetching access token...")
    access_token = fetch_access_token()
    print("Token received.")

    print("\nCalling MCP Retrieve tool...")

    result = retrieve(
        GATEWAY_URL,
        access_token,
        question,
    )

    print("\nParsed response:")
    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
