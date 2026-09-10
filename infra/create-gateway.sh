#!/usr/bin/env bash
#
# Create (or re-create) the AgentCore Gateway that fronts the SFOE public
# energy knowledge base, and point it at the MCP server Lambda.
#
# Idempotent: re-running finds the existing gateway/target by name and updates
# it rather than creating a second one. Safe to run repeatedly.
#
#   ./infra/create-gateway.sh                       # create or update
#   ./infra/create-gateway.sh --show                # print state, change nothing
#   ./infra/create-gateway.sh --keep-legacy-target  # leave the old connector
#
# ARCHITECTURE, because this reversed direction and the old shape is still
# described in older commits. The gateway used to sit BEHIND the MCP server:
# the server minted a Cognito token and called a `bedrock-knowledge-bases`
# connector target here, which forwarded to Bedrock Retrieve. It now sits in
# FRONT of it:
#
#   client --(no credential)--> gateway --(Lambda Invoke API)--> MCP server Lambda
#                                                                  |
#                                                                  +--(IAM role)
#                                                                      --> Bedrock
#
# So this file no longer configures retrieval at all. The server owns the
# Retrieve call and its parameters; see mcp-gateway/knowledge_base.py. What
# this file owns is who may reach the gateway (by default: anyone), how the
# gateway reaches the server (a direct Lambda invocation), which tools it
# advertises, and which MCP versions are negotiable.
#
# The unauthenticated inbound leg is deliberate and is the one setting most
# likely to look like an oversight, so it is argued for at AUTHORIZER_TYPE
# below rather than here.
#
# WHY THE GATEWAY INVOKES THE LAMBDA INSTEAD OF SPEAKING MCP TO IT. An
# `mcp.mcpServer` target pointed at the Lambda's IAM-authorised Function URL is
# the design this obviously wants, and it does not work: AgentCore's outbound
# SigV4 signer signs POST bodies as empty, so a Function URL rejects every
# request with a signature mismatch before the function runs. MCP's streamable
# HTTP transport is POST-only, so there is nothing to route around inside that
# design. mcp-gateway/lambda_handler.py records the evidence in full.
#
# The cost of the workaround is that the tool catalogue is no longer discovered.
# A Lambda target carries a static copy of it, which is why this script imports
# the server and generates one -- see TOOL SCHEMA below.
#
# Prerequisites, all of which already exist and are NOT created here because
# they outlive the gateway (deleting and recreating a gateway must not
# invalidate the credentials that clients are already using):
#
#   - the IAM role the gateway assumes (outbound auth)
#   - the Cognito user pool + client_credentials app client -- unused on the
#     default open path, needed only by AUTHORIZER_TYPE=CUSTOM_JWT. Kept
#     because it is the fallback if open access has to be withdrawn.
#   - the Lambda and the grant that lets the gateway role invoke it -- both
#     ./infra/deploy-lambda.sh's job. Run that first; this script checks for
#     the grant and refuses without it.
#
# Requires aws-cli >= 2.30 (earlier versions have no bedrock-agentcore-control
# at all -- not a missing flag, a missing service). Set AWS to point at a
# specific binary if the one on PATH is older.
#
# On macOS 26.x, a Homebrew aws-cli dies on any real command with
#
#   Symbol not found: _XML_SetAllocTrackerActivationThreshold
#
# That is Homebrew's python@3.14, not aws-cli, and reinstalling aws-cli cannot
# fix it. See the header of ./infra/deploy-lambda.sh for the diagnosis and the
# wrapper that works around it -- note that exporting DYLD_LIBRARY_PATH before
# running this script does NOT work, because SIP strips it on exec.

set -euo pipefail

AWS="${AWS:-aws}"
REGION="${AWS_REGION:-eu-central-1}"

# The interpreter used to generate the tool catalogue. It has to be one that
# can import the MCP server, i.e. one with mcp-gateway/requirements.txt
# installed -- typically a virtualenv rather than the system python3.
PYTHON="${PYTHON:-python3}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_DIR="$REPO_ROOT/mcp-gateway"

# Resolved against the caller's directory NOW, because the catalogue step runs
# from $SERVER_DIR. `PYTHON=.venv/bin/python` -- which is what the failure
# message tells you to pass, and the obvious thing to type -- would otherwise
# be looked up inside mcp-gateway/ and not found. A bare name like `python3`
# is left alone so PATH lookup still works.
if [[ "$PYTHON" == */* && "$PYTHON" != /* ]]; then
  PYTHON="$(cd "$(dirname "$PYTHON")" && pwd)/$(basename "$PYTHON")"
fi

SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT

# --- inputs -----------------------------------------------------------------

# The open gateway. The name is deliberately NOT the original
# `bfe-energy-knowledge-gateway`, which still exists and still answers with
# CUSTOM_JWT, because AgentCore rejects an authorizer change on a live gateway:
#
#   ValidationException: Authorizer type cannot be updated for an existing gateway
#
# So going open meant a new gateway, and a new gateway means a new URL whatever
# it is called -- the URL is built from the generated gateway id, not the name.
# Given that the URL had to change anyway, standing a second gateway up beside
# the first costs nothing and keeps every existing client working while the open
# one is proven. Delete the old one when nothing needs it:
#
#   aws bedrock-agentcore-control delete-gateway \
#     --gateway-identifier bfe-energy-knowledge-gateway-knpw2atkay
#
# Override to point this script at a different gateway, e.g. to rebuild the
# authenticated one:
#
#   GATEWAY_NAME=bfe-energy-knowledge-gateway AUTHORIZER_TYPE=CUSTOM_JWT \
#     ./infra/create-gateway.sh
GATEWAY_NAME="${GATEWAY_NAME:-bfe-energy-knowledge-open}"
GATEWAY_DESCRIPTION="Open MCP gateway over the SFOE public energy knowledge base"

# Reused, not created here. The trust policy is scoped to the account rather
# than to one gateway ARN, so any gateway in this account can assume it.
ROLE_ARN="arn:aws:iam::542202863496:role/vl-bfe-kg-bfe-gateway"

# INBOUND AUTH. `NONE` means exactly what it says: the gateway answers anyone
# who knows the URL, with no token and no signature. That is the point -- this
# publishes public federal documents, and requiring a credential to read public
# data makes the thing harder to consume without making it safer.
#
# CUSTOM_JWT is the alternative and is one word away:
#
#   AUTHORIZER_TYPE=CUSTOM_JWT ./infra/create-gateway.sh
#
# It is kept working, not kept around as decoration -- the gateway ran that way
# and may have to again if open access is abused. See "the cost of open" below.
AUTHORIZER_TYPE="${AUTHORIZER_TYPE:-NONE}"

# Only read when AUTHORIZER_TYPE is CUSTOM_JWT. The discovery URL must be the
# Cognito *OIDC* endpoint on cognito-idp.<region>.amazonaws.com -- NOT the
# hosted-domain URL that issues the token. And the app client goes in
# allowedClients, not allowedAudience: a Cognito client_credentials token
# carries `client_id` and no `aud` claim, so an allowedAudience check can never
# match and every request 403s.
USER_POOL_ID="eu-central-1_DOer30qrA"
CLIENT_ID="3bb2sgsr2urspuke653u3otrcq"

# THE COST OF OPEN, stated here because it is a real one and the mitigation
# lives in another file. With no caller identity there is no per-caller brake:
# a runaway client and a popular launch look identical, and the only response
# to either is ./infra/deploy-lambda.sh --pause, which cuts everyone off. The
# reserved-concurrency cap on the Lambda is therefore the ONLY thing standing
# between a retry loop and an unbounded Bedrock bill. Do not raise it casually,
# and see the concurrency section of mcp-gateway/README.md before you do.

# DEBUG returns full upstream error detail to the caller instead of a generic
# message. It is on because it is the only way to see why a tool call failed --
# a raised ToolInvocationError from the Lambda is otherwise reported as an
# opaque 500.
#
# It also means internal identifiers -- ARNs, target ids, upstream error text --
# reach ANY caller, and with an open gateway that is anyone at all. This is the
# setting to turn off first if this outlives the hackathon.
EXCEPTION_LEVEL="DEBUG"

# The target: the MCP server itself.
#
# This name is a user-visible prefix, not an internal label. Every tool the
# server exports is surfaced to clients as `${TARGET_NAME}___<tool_name>`, so
# `search_energy_knowledge` becomes `bfe-energy___search_energy_knowledge`.
# Renaming the target renames every tool, which breaks any agent prompt or
# allow-list that names one. Treat it as an interface, not a comment.
TARGET_NAME="bfe-energy"

LAMBDA_NAME="${LAMBDA_NAME:-bfe-mcp-gateway}"

# The target of the old architecture. Deleted on every run unless
# --keep-legacy-target is passed, because leaving it in place would show
# clients a `bfe-public-knowledge___Retrieve` tool that returns raw Bedrock
# passages -- no dedup, no source URLs, no date parsing -- next to the real
# ones, and nothing in the tool description would tell an agent which to pick.
LEGACY_TARGET_NAME="bfe-public-knowledge"

# ----------------------------------------------------------------------------

aws_ac() { "$AWS" bedrock-agentcore-control --region "$REGION" "$@"; }
aws_lambda_() { "$AWS" lambda --region "$REGION" "$@"; }

find_gateway() {
  aws_ac list-gateways --output json \
    | python3 -c "import sys,json;print(next((g['gatewayId'] for g in json.load(sys.stdin)['items'] if g['name']=='$GATEWAY_NAME'),''))"
}

find_target() {
  # $1 gateway id, $2 target name
  aws_ac list-gateway-targets --gateway-identifier "$1" --output json \
    | python3 -c "import sys,json;print(next((t['targetId'] for t in json.load(sys.stdin)['items'] if t['name']=='$2'),''))"
}

target_status() {
  aws_ac get-gateway-target --gateway-identifier "$1" --target-id "$2" --output json \
    | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("status",""),"|"," ".join(d.get("statusReasons") or []))'
}

# The kind of target that already exists under a given name -- "lambda",
# "mcpServer", "connector", and so on. A target's type cannot be changed in
# place, so this is what decides between updating one and replacing it.
#
# Prints nothing if the shape is not recognised, which the caller treats as an
# error rather than as "not a lambda target". Guessing the other way would make
# an unexpected API response delete and recreate a working target on every run.
target_kind() {
  aws_ac get-gateway-target --gateway-identifier "$1" --target-id "$2" --output json \
    | python3 -c 'import sys,json;print(next(iter(json.load(sys.stdin).get("targetConfiguration",{}).get("mcp",{})),""))'
}

# The function ARN, discovered rather than hardcoded, so that a function
# recreated in another account or region cannot be silently pointed at.
lambda_arn() {
  aws_lambda_ get-function-configuration --function-name "$LAMBDA_NAME" --output json \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["FunctionArn"])'
}

# The gateway invokes the function directly, so the function must let it. Two
# grants exist and either would do on its own (IAM ORs identity-based and
# resource-based policy within an account); only the resource-side one is
# checkable from here, since reading the gateway role's inline policies needs
# iam:GetRolePolicy, which this caller may not have. So a pass here is
# conclusive and a failure is not -- hence the wording of the error.
#
# Worth the preflight regardless: without a grant the target is created quite
# happily and every tool call fails at runtime with an error that names neither
# the missing permission nor the principal.
require_invoke_grant() {
  local policy
  policy="$(aws_lambda_ get-policy --function-name "$LAMBDA_NAME" --output json 2>/dev/null || true)"
  if [[ -z "$policy" ]]; then
    echo "error: $LAMBDA_NAME has no resource policy at all." >&2
    echo "       run ./infra/deploy-lambda.sh first." >&2
    exit 1
  fi
  # The policy document arrives as a JSON string nested inside a JSON object,
  # so it needs decoding twice. Principal is a bare string for a service and a
  # {"AWS": ...} object for a role; Action is a string or a list, and is
  # compared exactly rather than by substring -- "lambda:InvokeFunctionUrl"
  # contains "lambda:InvokeFunction" and grants something else entirely.
  if ! printf '%s' "$policy" | python3 -c '
import json, sys
role = sys.argv[1]
wanted = {"lambda:InvokeFunction", "lambda:*", "*"}
doc = json.loads(json.load(sys.stdin)["Policy"])
for s in doc.get("Statement", []):
    if s.get("Effect") != "Allow":
        continue
    actions = s.get("Action", [])
    actions = [actions] if isinstance(actions, str) else actions
    if role in json.dumps(s.get("Principal", "")) and wanted.intersection(actions):
        sys.exit(0)
sys.exit(1)
' "$ROLE_ARN"; then
    echo "error: the resource policy on $LAMBDA_NAME does not grant" >&2
    echo "       lambda:InvokeFunction to $ROLE_ARN." >&2
    echo "       run ./infra/deploy-lambda.sh -- it adds both halves of the grant." >&2
    exit 1
  fi
}

# TOOL SCHEMA. A Lambda target does not talk MCP to the function, so the
# gateway has to be handed the tool catalogue instead of discovering it. It is
# generated from the server itself (mcp-gateway/tool_schema.py imports the same
# MCPServer the tools register on) rather than written out here, because a
# hand-maintained copy would drift the first time a parameter changed.
#
# The catalogue therefore describes the WORKING TREE, not the deployed image.
# Run ./infra/deploy-lambda.sh and this script from the same checkout, in that
# order, which is what deploy-lambda.sh's closing message tells you to do.
TOOL_SCHEMA="$SCRATCH/tools.json"

generate_tool_schema() {
  if ! ( cd "$SERVER_DIR" && "$PYTHON" tool_schema.py ) \
       > "$TOOL_SCHEMA" 2>"$SCRATCH/tools.err"; then
    echo "error: could not generate the tool catalogue." >&2
    sed 's/^/       /' "$SCRATCH/tools.err" >&2
    echo "       tool_schema.py imports the server, so it needs the project's" >&2
    echo "       dependencies. If they are in a virtualenv, name it:" >&2
    echo "         PYTHON=.venv/bin/python ./infra/create-gateway.sh" >&2
    exit 1
  fi
  # An empty catalogue is accepted by the service and produces a gateway that
  # advertises nothing -- a failure that looks exactly like success until a
  # client calls tools/list.
  local count
  count="$(python3 -c 'import json,sys;print(len(json.load(open(sys.argv[1]))))' "$TOOL_SCHEMA")"
  if (( count == 0 )); then
    echo "error: the server exports no tools; refusing to publish an empty gateway." >&2
    exit 1
  fi
  echo "==> tool catalogue: $count tool(s) generated from $SERVER_DIR"
  python3 -c '
import json, sys
for tool in json.load(open(sys.argv[1])):
    print("    " + tool["name"] + "(" +
          ", ".join(tool["inputSchema"].get("properties", {})) + ")")
' "$TOOL_SCHEMA"
}

authorizer_configuration() {
  cat <<JSON
{
  "customJWTAuthorizer": {
    "discoveryUrl": "https://cognito-idp.${REGION}.amazonaws.com/${USER_POOL_ID}/.well-known/openid-configuration",
    "allowedClients": ["${CLIENT_ID}"]
  }
}
JSON
}

# `authorizerConfiguration` is required for CUSTOM_JWT and rejected for NONE,
# so the flag pair is built rather than always passed. Bash arrays, not a
# string: a quoted-string "$FLAGS" would arrive as one argument and an unquoted
# one would word-split the JSON on every space in it.
authorizer_flags() {
  AUTHORIZER_FLAGS=(--authorizer-type "$AUTHORIZER_TYPE")
  if [[ "$AUTHORIZER_TYPE" == "CUSTOM_JWT" ]]; then
    AUTHORIZER_FLAGS+=(--authorizer-configuration "$(authorizer_configuration)")
  fi
}

# Without an explicit protocolConfiguration a gateway negotiates only MCP
# 2025-03-26, and a client that asks for a newer version is refused outright
# with "Unsupported protocol version" -- so this list is what decides whether
# an existing client can talk to the gateway at all. Older versions stay in
# the list on purpose: dropping one breaks any client pinned to it.
#
# This is the INBOUND list (gateway <- client). The outbound leg
# (gateway -> our server) negotiates separately, and AgentCore supports
# 2026-07-28, 2025-11-25, 2025-06-18 and 2025-03-26 there.
protocol_configuration() {
  cat <<'JSON'
{
  "mcp": {
    "supportedVersions": ["2025-03-26", "2025-11-25", "2026-07-28"],
    "instructions": "Tools over the Swiss Federal Office of Energy (SFOE/BFE) public energy publications: ~2400 PDF reports, studies and statistics on Swiss energy, published roughly 2020-2026. Passages come back ranked, each attributed to its source document with the publication date and, where it could be resolved, a public download URL. The corpus is multilingual (German, French, Italian, English) and a query in one language will return passages in the others. Page numbers are not available in this corpus.",
    "streamingConfiguration": {"enableResponseStreaming": false}
  }
}
JSON
}

# An `mcp.lambda` target: the gateway invokes the function through the Lambda
# Invoke API, passing the tool's arguments as the event and the tool's name in
# the client context as `bedrockAgentCoreToolName`, prefixed with this target's
# name. mcp-gateway/lambda_handler.py is the other side of that contract.
#
# The tool catalogue is inlined rather than kept in S3. The `s3` variant of
# ToolSchema exists for catalogues too large to send, and would add a bucket,
# an object lifecycle and a second read grant on the gateway role to maintain
# for three tools.
#
# There is no listingMode here and nothing to synchronize: the catalogue below
# IS the list the gateway serves. Changing a tool means re-running this script,
# which is also what CreateGatewayTarget/UpdateGatewayTarget already do.
#
# `resourcePriority` is left at its default (1000); it only matters when two
# targets claim the same resource URI, and there is one target.
target_configuration() {
  python3 -c '
import json, sys
tools = json.load(open(sys.argv[1]))
print(json.dumps({"mcp": {"lambda": {
    "lambdaArn": sys.argv[2],
    "toolSchema": {"inlinePayload": tools},
}}}))
' "$TOOL_SCHEMA" "$LAMBDA_ARN"
}

# GATEWAY_IAM_ROLE alone: the gateway calls Lambda as its own service role.
#
# No `credentialProvider` object accompanies it. The iamCredentialProvider that
# an mcp.mcpServer target needs -- which names the SigV4 service being signed
# for -- has nothing to name here, because the gateway is calling an AWS API
# rather than an arbitrary HTTPS endpoint. The field is optional in the service
# model; sending an empty one is a validation error, not a no-op.
credential_provider_configurations() {
  cat <<'JSON'
[
  {"credentialProviderType": "GATEWAY_IAM_ROLE"}
]
JSON
}

# A target's type is fixed at creation: an mcp.mcpServer target cannot be
# updated into an mcp.lambda one, and the attempt leaves it in
# UPDATE_UNSUCCESSFUL. The same is true of a target that is already stuck
# there. Both cases are handled by replacing it, which is safe because a target
# holds no state -- everything in it is generated by this script.
replace_target() {
  local gw="$1" tid="$2" why="$3" i=0
  echo "==> replacing target $TARGET_NAME ($tid): $why"
  aws_ac delete-gateway-target \
    --gateway-identifier "$gw" --target-id "$tid" --output json > /dev/null
  # Deletion is asynchronous, and creating the replacement while the old one
  # still holds the name fails with a conflict.
  while (( i < 60 )); do
    [[ -n "$(find_target "$gw" "$TARGET_NAME")" ]] || return 0
    sleep 2
    (( i += 1 ))
  done
  echo "error: target $tid still exists two minutes after deletion" >&2
  return 1
}

# Target creation is asynchronous (the call returns 202), so the status is the
# only place a permissions or configuration failure surfaces. Waiting here is
# what turns "the script succeeded but the tools are missing" into an error
# message.
wait_for_target() {
  local gw="$1" tid="$2" i=0 status reason line
  while (( i < 60 )); do
    line="$(target_status "$gw" "$tid")"
    status="${line%% |*}"
    reason="${line#* | }"
    case "$status" in
      READY)
        echo "    target READY"
        return 0
        ;;
      *PENDING_AUTH)
        # Only reachable with an authorization-code OAuth provider, which this
        # target does not use. Named anyway: without it the loop would spin for
        # two minutes and then report a timeout, which points at the wrong
        # problem entirely.
        echo "error: target is waiting for an authorization flow ($status)." >&2
        echo "       that should not happen with a GATEWAY_IAM_ROLE provider." >&2
        return 1
        ;;
      DELETING)
        echo "error: target is being deleted -- something else is changing it" >&2
        return 1
        ;;
      *FAILED*|*UNSUCCESSFUL*)
        echo "error: target ended in status $status" >&2
        # An if, not `[[ ... ]] && echo`: with an empty reason that AND list
        # is a failing command and `set -e` would kill the script before the
        # caller ever sees the return code.
        if [[ -n "$reason" ]]; then echo "       $reason" >&2; fi
        return 1
        ;;
    esac
    sleep 2
    (( i += 1 ))
  done
  echo "warning: target still in status '$status' after 2 minutes; check with --show" >&2
  return 0
}

# ----------------------------------------------------------------------------

MODE="${1:-}"
GATEWAY_ID="$(find_gateway)"

if [[ "$MODE" == "--show" ]]; then
  [[ -n "$GATEWAY_ID" ]] || { echo "gateway '$GATEWAY_NAME' does not exist"; exit 0; }
  aws_ac get-gateway --gateway-identifier "$GATEWAY_ID" --output json
  echo
  aws_ac list-gateway-targets --gateway-identifier "$GATEWAY_ID" --output json
  exit 0
fi

if [[ -n "$MODE" && "$MODE" != "--keep-legacy-target" ]]; then
  echo "unknown argument: $MODE" >&2
  # Pulled out by pattern rather than by line number, so editing the header
  # cannot silently turn the usage message into unrelated prose.
  grep '^#   \./infra' "${BASH_SOURCE[0]}" | sed 's/^# //' >&2
  exit 2
fi

require_invoke_grant
LAMBDA_ARN="$(lambda_arn)"
echo "==> MCP server: $LAMBDA_ARN"
generate_tool_schema
authorizer_flags

if [[ -z "$GATEWAY_ID" ]]; then
  echo "==> creating gateway $GATEWAY_NAME (inbound auth: $AUTHORIZER_TYPE)"
  GATEWAY_ID="$(aws_ac create-gateway \
    --name "$GATEWAY_NAME" \
    --description "$GATEWAY_DESCRIPTION" \
    --role-arn "$ROLE_ARN" \
    --protocol-type MCP \
    --protocol-configuration "$(protocol_configuration)" \
    "${AUTHORIZER_FLAGS[@]}" \
    --exception-level "$EXCEPTION_LEVEL" \
    --output json | python3 -c 'import sys,json;print(json.load(sys.stdin)["gatewayId"])')"
else
  # update-gateway replaces the whole configuration, so re-running this script
  # brings an existing gateway back in line with what is written here.
  echo "==> gateway $GATEWAY_NAME exists ($GATEWAY_ID), updating (inbound auth: $AUTHORIZER_TYPE)"
  # Captured rather than let through, because one specific failure has a
  # consequence worth spelling out: it is not established that the service
  # accepts an authorizerType change on an existing gateway. If it refuses,
  # the only route to the new setting is delete + recreate, and recreating
  # mints a NEW gatewayId -- so the gateway URL changes and every client
  # holding the old one breaks. That is a decision, not a retry, so the
  # script stops and says so instead of deleting anything on its own.
  if ! UPDATE_ERR="$(aws_ac update-gateway \
    --gateway-identifier "$GATEWAY_ID" \
    --name "$GATEWAY_NAME" \
    --description "$GATEWAY_DESCRIPTION" \
    --role-arn "$ROLE_ARN" \
    --protocol-type MCP \
    --protocol-configuration "$(protocol_configuration)" \
    "${AUTHORIZER_FLAGS[@]}" \
    --exception-level "$EXCEPTION_LEVEL" \
    --output json 2>&1 >/dev/null)"; then
    echo "error: update-gateway failed:" >&2
    echo "$UPDATE_ERR" | sed 's/^/       /' >&2
    CURRENT_AUTH="$(aws_ac get-gateway --gateway-identifier "$GATEWAY_ID" --output json 2>/dev/null \
      | python3 -c 'import sys,json;print(json.load(sys.stdin).get("authorizerType",""))' 2>/dev/null || true)"
    if [[ -n "$CURRENT_AUTH" && "$CURRENT_AUTH" != "$AUTHORIZER_TYPE" ]]; then
      echo >&2
      echo "       the gateway is currently $CURRENT_AUTH and this run asked for" >&2
      echo "       $AUTHORIZER_TYPE. If the error above says the authorizer cannot be" >&2
      echo "       changed, the only way through is to delete and recreate:" >&2
      echo >&2
      echo "         aws bedrock-agentcore-control delete-gateway --gateway-identifier $GATEWAY_ID" >&2
      echo "         ./infra/create-gateway.sh" >&2
      echo >&2
      echo "       READ THIS FIRST: a recreated gateway gets a new gatewayId, so the" >&2
      echo "       gateway URL changes and anything holding the old one stops working." >&2
      echo "       Nothing has been deleted -- that call is yours to make." >&2
    fi
    exit 1
  fi
fi

TARGET_ID="$(find_target "$GATEWAY_ID" "$TARGET_NAME")"

if [[ -n "$TARGET_ID" ]]; then
  KIND="$(target_kind "$GATEWAY_ID" "$TARGET_ID")"
  STATUS_LINE="$(target_status "$GATEWAY_ID" "$TARGET_ID")"
  STATUS="${STATUS_LINE%% |*}"
  if [[ -z "$KIND" ]]; then
    echo "error: cannot tell what kind of target $TARGET_NAME ($TARGET_ID) is." >&2
    echo "       inspect it with ./infra/create-gateway.sh --show and delete it" >&2
    echo "       by hand if it is not an mcp.lambda target." >&2
    exit 1
  elif [[ "$KIND" != "lambda" ]]; then
    replace_target "$GATEWAY_ID" "$TARGET_ID" "it is an mcp.${KIND} target"
    TARGET_ID=""
  elif [[ "$STATUS" == *UNSUCCESSFUL* || "$STATUS" == *FAILED* ]]; then
    replace_target "$GATEWAY_ID" "$TARGET_ID" "it is stuck in $STATUS"
    TARGET_ID=""
  fi
fi

if [[ -z "$TARGET_ID" ]]; then
  echo "==> creating target $TARGET_NAME"
  TARGET_ID="$(aws_ac create-gateway-target \
    --gateway-identifier "$GATEWAY_ID" \
    --name "$TARGET_NAME" \
    --target-configuration "$(target_configuration)" \
    --credential-provider-configurations "$(credential_provider_configurations)" \
    --output json | python3 -c 'import sys,json;print(json.load(sys.stdin)["targetId"])')"
else
  echo "==> updating target $TARGET_NAME ($TARGET_ID)"
  aws_ac update-gateway-target \
    --gateway-identifier "$GATEWAY_ID" \
    --target-id "$TARGET_ID" \
    --name "$TARGET_NAME" \
    --target-configuration "$(target_configuration)" \
    --credential-provider-configurations "$(credential_provider_configurations)" \
    --output json > /dev/null
fi

wait_for_target "$GATEWAY_ID" "$TARGET_ID"

# The old connector target. Removed by default -- see LEGACY_TARGET_NAME.
if [[ "$MODE" != "--keep-legacy-target" ]]; then
  LEGACY_ID="$(find_target "$GATEWAY_ID" "$LEGACY_TARGET_NAME")"
  if [[ -n "$LEGACY_ID" ]]; then
    echo "==> deleting legacy connector target $LEGACY_TARGET_NAME ($LEGACY_ID)"
    echo "    this removes the ${LEGACY_TARGET_NAME}___Retrieve tool from the gateway"
    aws_ac delete-gateway-target \
      --gateway-identifier "$GATEWAY_ID" --target-id "$LEGACY_ID" --output json > /dev/null
  fi
fi

GATEWAY_URL="$(aws_ac get-gateway --gateway-identifier "$GATEWAY_ID" --output json \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["gatewayUrl"])')"

if [[ "$AUTHORIZER_TYPE" == "NONE" ]]; then
  INBOUND="NONE -- this URL is open; anyone who has it can call the tools"
else
  INBOUND="$AUTHORIZER_TYPE"
fi

cat <<SUMMARY

done.
  gateway id   : $GATEWAY_ID
  target id    : $TARGET_ID
  target       : $LAMBDA_ARN (invoked directly, no HTTP)
  inbound auth : $INBOUND
  GATEWAY_URL  : $GATEWAY_URL

The gateway exports the tools listed above, each prefixed '${TARGET_NAME}___'.
Check what a client actually sees with:

  python3 list_tools.py

The catalogue is a static copy, so a change to any tool's name, description or
arguments needs BOTH steps, in this order and from this checkout:

  ./infra/deploy-lambda.sh && ./infra/create-gateway.sh
SUMMARY
