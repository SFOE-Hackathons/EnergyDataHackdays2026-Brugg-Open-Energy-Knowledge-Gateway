#!/usr/bin/env bash
#
# Deploy the MCP server as a Lambda function behind an AgentCore Gateway.
#
# Builds the container for arm64, pushes it to ECR, creates or updates the
# Lambda function, caps its concurrency, and grants the gateway's IAM role --
# and nothing else -- permission to invoke it.
#
#   ./infra/deploy-lambda.sh            # build, push, create or update, verify
#   ./infra/deploy-lambda.sh --show     # print current state, change nothing
#   ./infra/deploy-lambda.sh --pause    # kill switch: refuse all traffic
#   ./infra/deploy-lambda.sh --resume   # undo --pause
#   ./infra/deploy-lambda.sh --destroy  # tear down everything this created
#
# This script does NOT create the gateway. Run infra/create-gateway.sh after
# this one; it registers this function as the gateway's Lambda target.
#
# Idempotent: every step checks for what it is about to create and updates it
# instead. Safe to run repeatedly, and re-running is the way to ship a change.
#
# WHY LAMBDA. Of the compute this account can actually use, it is the only
# option with a numeric, enforceable concurrency ceiling -- see
# RESERVED_CONCURRENCY below, which is the actual reason it was chosen -- and
# the only one that costs nothing while idle.
#
# WHY THERE IS NO ENDPOINT. This function has no Function URL and no HTTP route
# of any kind. The gateway calls it through the Lambda Invoke API, handing the
# tool's arguments over as the invocation event; the public face of the service
# is the gateway's Cognito-gated MCP endpoint, not anything here.
#
# That is the fourth design, and the three it replaced are worth recording so
# nobody re-derives them:
#
#   1. An open Function URL (--auth-type NONE plus a resource policy granting
#      lambda:InvokeFunctionUrl to Principal "*"). Every request came back 403
#      with no log group ever created -- denied at the authorization layer,
#      before the function ran, while a direct `lambda invoke` of the same image
#      returned a valid MCP response. The account belongs to AWS Organization
#      o-3r6tcb49g2 (BFE), whose guardrails a member account cannot read;
#      their shape shows indirectly -- creating an API Gateway fails with an
#      explicit deny naming .../service_control_policy/p-bvj3x4lc, and probing
#      shows API Gateway, ALB, App Runner, Lightsail and Amplify all denied
#      while Lambda, ECS, EC2, S3 and CloudFront are allowed. Anonymous
#      principals invoking Lambda are denied by the same class of guardrail.
#
#   2. CloudFront with an Origin Access Control of type "lambda", which signs
#      each origin request as a service principal and so has no anonymous
#      invoke to deny. It worked, and is recorded in mcp-gateway/README.md, but
#      once the gateway moved in front it was signing SigV4 itself -- leaving
#      the CDN, its OAC and a Lambda@Edge function that hashed POST bodies as
#      pure cost. Roughly 250 lines of this script went with them.
#
#   3. A Function URL with AWS_IAM auth, invoked by the gateway as an
#      mcp.mcpServer target. This one fails for a reason no configuration can
#      fix: AgentCore's outbound signer computes the SigV4 signature over an
#      *empty* payload while sending the request body, so a Function URL --
#      which verifies the payload hash -- rejects every POST with a signature
#      mismatch, and MCP's streamable HTTP transport is POST-only. Evidence:
#      the target stuck in UPDATE_UNSUCCESSFUL with "Authorization error when
#      sending message"; UrlRequestCount 2, Url4xxCount 2, Invocations 0; the
#      same handshake signed correctly by hand returning 200/202/200 against
#      the same endpoint. See mcp-gateway/lambda_handler.py.
#
# Any URL on this function is therefore not just unused but a second entry
# point that skips the gateway's authorizer, which is why the deploy deletes
# one if it finds it rather than leaving it switched off.
#
# WHY THERE ARE NO SECRETS HERE ANY MORE. An earlier revision read CLIENT_ID
# and CLIENT_SECRET out of .env and shipped them as Lambda environment
# variables, with a good deal of care taken to keep them out of argv and out of
# the shell history. None of that is needed now: the server calls
# bedrock-agent-runtime:Retrieve under its own execution role, so upstream
# access is the IAM grant attached below rather than a credential travelling
# with the deployment. This script no longer reads .env at all.
#
# A real consequence, not just tidiness: an IAM-based AWS Budgets action can
# now actually throttle upstream spend by denying the execution role. Against
# the Cognito design it could not have blocked a single retrieval.
#
# Requirements: docker with buildx, python3, and aws-cli v2.
#
# On macOS 26.x a Homebrew aws-cli dies on any real command with
#
#   Symbol not found: _XML_SetAllocTrackerActivationThreshold
#
# That is Homebrew's python@3.14, not aws-cli: its `pyexpat` was built against
# an SDK whose libexpat exports that symbol, and the dyld shared cache on 26.x
# ships an older one. Reinstalling aws-cli cannot fix it -- `aws --version`
# still works, which makes it look healthy, because that path never imports
# pyexpat.
#
# Homebrew's own expat does export the symbol, so pointing dyld at it works --
# but NOT by exporting DYLD_LIBRARY_PATH before running this script. SIP strips
# every DYLD_* variable on exec of a protected binary, and `#!/usr/bin/env bash`
# is one, so the variable is gone before the first line runs. It has to be set
# on the inside. A three-line wrapper does it:
#
#   #!/bin/sh
#   export DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib
#   exec /opt/homebrew/bin/aws "$@"
#
# then `AWS=/path/to/wrapper ./infra/deploy-lambda.sh`. Installing the official
# AWS pkg, which bundles its own Python, avoids the whole problem.

set -euo pipefail

AWS="${AWS:-aws}"
REGION="${AWS_REGION:-eu-central-1}"
ACCOUNT_ID="542202863496"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_CONTEXT="$REPO_ROOT/mcp-gateway"

# --- inputs -----------------------------------------------------------------

NAME="bfe-mcp-gateway"
ROLE_NAME="${NAME}-exec"
ECR_REPO="$NAME"
IMAGE_TAG="${IMAGE_TAG:-latest}"

REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE_URI="${REGISTRY}/${ECR_REPO}:${IMAGE_TAG}"

# The knowledge base the server retrieves from. Not a secret, so it goes on the
# command line: having it visible in `get-function-configuration` is worth more
# than the nothing it would gain from being hidden. mcp-gateway/config.py
# defaults to the same value, so the function works if it is ever unset.
KNOWLEDGE_BASE_ID="${KNOWLEDGE_BASE_ID:-ZPVWAEHXNB}"
KNOWLEDGE_BASE_ARN="arn:aws:bedrock:${REGION}:${ACCOUNT_ID}:knowledge-base/${KNOWLEDGE_BASE_ID}"

# The AgentCore Gateway's service role, created outside this repo and shared
# with infra/create-gateway.sh. It is the ONLY principal allowed to invoke this
# function, so this ARN is what makes the tools reachable at all.
GATEWAY_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/vl-bfe-kg-bfe-gateway"

# Grants bedrock:Retrieve on the one knowledge base and nothing else. Inline
# rather than managed: it is meaningless outside this function, and an inline
# policy is deleted with the role instead of being left behind.
RETRIEVE_POLICY_NAME="${NAME}-kb-read"

# arm64 because Lambda runs Graviton natively and the development machines are
# Apple Silicon, so this is the one architecture that needs no cross-build in
# either direction. It is also ~20% cheaper per GB-second.
ARCHITECTURE="arm64"

# The default is 3 seconds, which every get_metric_timeline call would blow
# through. Measured worst case for a 5-year, 25-per-year request is ~9s; 60
# leaves room for a cold start on top of a cold public-source cache without
# being so generous that a wedged request bills for minutes.
TIMEOUT_SECONDS=60

# This is really a CPU setting, not a memory setting, and it is a compromise.
#
# The working set is settled and small. Measured on this image under three
# concurrent worst-case requests (detail=full, source_fields=all, 5 years x 25
# per year): 53 MB idle, 76 MB peak, ~19% of one core for all three together.
# Lambda runs one request per execution environment, so the figure that applies
# is the single-request one, ~65 MB. 512 leaves ~6.7x headroom; 128 would very
# likely also work.
#
# What argues against going lower is that Lambda ties CPU to memory linearly --
# a full vCPU arrives at 1769 MB, so this is ~0.29 vCPU and 128 MB would be
# ~0.07. The function is I/O-bound in steady state (it waits on Bedrock), so
# more CPU does NOT shorten a request, and since billing is per GB-second,
# over-provisioning multiplies the bill for identical wall clock. But cold
# start is CPU-bound: unpacking a ~204 MB image and importing fastmcp/pydantic/
# starlette/boto3 is real work, and starving it shows up as a slow first
# request.
#
# So: steady state wants 128, cold start wants more, and 512 splits it.
#
# SETTLE THIS WITH DATA ON THE FIRST DEPLOY. Every invocation logs a REPORT
# line carrying both numbers:
#
#   aws logs tail /aws/lambda/bfe-mcp-gateway --region eu-central-1 \
#     --filter-pattern REPORT
#
# "Max Memory Used" gives the true working set (expect ~80-120 MB, since
# Lambda's runtime overhead sits on top of what was measured above) and "Init
# Duration" gives the cold start. If Init Duration is comfortable, halve this;
# if it is not, raise it. Do not guess a second time.
MEMORY_MB="${MEMORY_MB:-512}"

# THE MOST IMPORTANT LINE IN THIS FILE.
#
# The hosting bill is not the exposure. Every inbound request fans out into
# Bedrock Retrieve calls on this account -- up to 5 for get_metric_timeline
# (one per year) and 5 for get_chart_data -- and that cost is identical no
# matter what hosts the proxy. Capping the container's price caps only the
# container.
#
# Lambda's default account concurrency is 1000. Without this cap AWS will
# autoscale, on a caller's behalf, to as many as 1000 concurrent invocations
# x 5 upstream retrievals = 5000 concurrent Bedrock calls, and the first sign
# of it is the bill. The gateway's Cognito authorizer means a caller has to
# hold credentials to get that far, which is a real improvement over the open
# endpoint this cap was originally written for -- but credentials get shared,
# and one badly-written agent in a retry loop needs no malice at all.
#
# Reserved concurrency is a hard, enforced ceiling: at most this many requests
# run at once, and everything beyond is throttled immediately
# (TooManyRequestsException, HTTP 429) -- there is no queue. It is set further
# down BEFORE the gateway is granted permission to invoke, and that ordering is
# deliberate: the cap exists before anything can call.
#
# Do not read this as a request-rate limit. AWS's "max rps = 10x reserved
# concurrency" only holds for sub-100ms functions; here throughput is
# concurrency divided by duration, and these requests are slow. At 5 slots:
# ~5-7 req/s for search (~1s each) but only ~0.6-1.2 req/s for
# get_metric_timeline and get_chart_data (~4-9s each).
#
# The consequence to weigh before a demo: twenty people calling the timeline
# tool at once means five run and fifteen get 429s. Raise this for a demo
# window (RESERVED_CONCURRENCY=15 ./infra/deploy-lambda.sh --resume) and drop
# it back afterwards -- each slot is worth up to 5 concurrent Bedrock calls.
RESERVED_CONCURRENCY="${RESERVED_CONCURRENCY:-5}"

# ----------------------------------------------------------------------------

lambda_() { "$AWS" lambda --region "$REGION" "$@"; }
ecr_() { "$AWS" ecr --region "$REGION" "$@"; }
jqp() { python3 -c "import sys,json;d=json.load(sys.stdin);$1"; }

note() { echo "==> $*"; }

SCRATCH="$(mktemp -d)"
chmod 700 "$SCRATCH"
cleanup() { rm -rf "$SCRATCH"; }
trap cleanup EXIT

function_exists() {
  lambda_ get-function --function-name "$NAME" >/dev/null 2>&1
}

# This function is not supposed to have a URL any more -- the gateway invokes
# it through the Lambda Invoke API. The helper stays so that a deploy run can
# delete a leftover one and --show can report it if it comes back.
function_url() {
  lambda_ get-function-url-config --function-name "$NAME" --output json 2>/dev/null \
    | jqp 'print(d["FunctionUrl"])' 2>/dev/null || true
}

# True if the function's resource policy carries a statement with exactly this
# Sid. A function with no policy at all has no statements, which is a false
# rather than an error -- that is the state of a freshly created function.
has_statement() {
  lambda_ get-policy --function-name "$NAME" --output json 2>/dev/null \
    | python3 -c '
import json, sys
raw = sys.stdin.read().strip()
if not raw:
    sys.exit(1)
doc = json.loads(json.loads(raw)["Policy"])
sys.exit(0 if any(s.get("Sid") == sys.argv[1] for s in doc.get("Statement", [])) else 1)
' "$1"
}

function_url_or_none() {
  local url
  url="$(function_url)"
  if [[ -n "$url" ]]; then
    echo "$url  <-- UNEXPECTED, re-run this script to remove it"
  else
    echo "none (correct -- invoked through the Lambda API)"
  fi
}

# --- subcommands ------------------------------------------------------------

if [[ "${1:-}" == "--show" ]]; then
  if ! function_exists; then
    echo "function '$NAME' does not exist in $REGION"
    exit 0
  fi
  lambda_ get-function-configuration --function-name "$NAME" --output json \
    | jqp 'print("\n".join(f"  {k:22}: {d.get(k)}" for k in
        ("FunctionName","State","LastUpdateStatus","Architectures","Timeout",
         "MemorySize","CodeSize","Runtime","PackageType")))'
  echo "  reserved concurrency  : $(lambda_ get-function-concurrency \
        --function-name "$NAME" --output json | jqp 'print(d.get("ReservedConcurrentExecutions","(unset -- UNCAPPED)"))')"
  # Reported because a URL here is a finding, not a feature: it would be a
  # second entry point that bypasses the gateway's authorizer. A deploy run
  # removes it; --show is how you notice one reappeared.
  echo "  function url          : $(function_url_or_none)"
  echo "  knowledge base        : $KNOWLEDGE_BASE_ID"
  echo "  invokers              :"
  # Deliberately not written with jqp and an f-string. Escaping a quote inside
  # an f-string expression only parses on Python 3.12+, and this script has to
  # run on whatever python3 the operator happens to have.
  lambda_ get-policy --function-name "$NAME" --output json 2>/dev/null \
    | python3 -c '
import json, sys
for statement in json.loads(json.load(sys.stdin)["Policy"])["Statement"]:
    principal = statement.get("Principal")
    if isinstance(principal, dict):
        principal = principal.get("AWS") or principal.get("Service")
    print("    " + statement["Sid"] + ": " + str(principal)
          + " -> " + statement["Action"])
' 2>/dev/null || echo "    (none -- nothing may invoke this function)"
  exit 0
fi

if [[ "${1:-}" == "--pause" || "${1:-}" == "--resume" ]]; then
  if [[ "${1}" == "--pause" ]]; then
    LIMIT=0
  else
    LIMIT="$RESERVED_CONCURRENCY"
  fi
  # Reversible and instant: the function, its grants and its image are
  # untouched, only the number of invocations allowed to run at once changes.
  # At 0 every request is throttled before any code runs and before any Bedrock
  # call is made. This is the thing to reach for when something is being abused.
  lambda_ put-function-concurrency \
    --function-name "$NAME" --reserved-concurrent-executions "$LIMIT" \
    --output json >/dev/null
  note "reserved concurrency set to $LIMIT"
  exit 0
fi

if [[ "${1:-}" == "--destroy" ]]; then
  note "deleting function $NAME"
  lambda_ delete-function --function-name "$NAME" 2>/dev/null || true
  note "deleting role $ROLE_NAME"
  "$AWS" iam delete-role-policy --role-name "$ROLE_NAME" \
    --policy-name "$RETRIEVE_POLICY_NAME" 2>/dev/null || true
  "$AWS" iam detach-role-policy --role-name "$ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole 2>/dev/null || true
  "$AWS" iam delete-role --role-name "$ROLE_NAME" 2>/dev/null || true
  # The gateway role itself is owned outside this repo and stays; only the
  # inline policy this script added to it is withdrawn.
  note "revoking the gateway role's invoke grant on $NAME"
  "$AWS" iam delete-role-policy --role-name "${GATEWAY_ROLE_ARN##*/}" \
    --policy-name "${NAME}-invoke" 2>/dev/null || true
  note "deleting ECR repository $ECR_REPO"
  ecr_ delete-repository --repository-name "$ECR_REPO" --force 2>/dev/null || true
  echo
  echo "done. CloudWatch log group /aws/lambda/$NAME is left in place on"
  echo "purpose -- it is what you read to find out what happened."
  echo
  echo "The gateway is NOT touched: its target now points at a function that no"
  echo "longer exists. Run ./infra/create-gateway.sh --destroy too, or re-run"
  echo "this script and then create-gateway.sh to restore the pair."
  exit 0
fi

if [[ -n "${1:-}" ]]; then
  echo "unknown argument: $1" >&2
  # Pulled out by pattern rather than by line number, so editing the header
  # cannot silently turn the usage message into unrelated prose.
  grep '^#   \./infra' "${BASH_SOURCE[0]}" | sed 's/^# //' >&2
  exit 2
fi

# --- preflight --------------------------------------------------------------

# Checked early and explicitly, because the failure mode otherwise is a
# confusing error five steps in, after an image has already been pushed.
if ! "$AWS" sts get-caller-identity >/dev/null 2>&1; then
  echo "the AWS CLI at '$AWS' cannot authenticate (or cannot start at all)." >&2
  echo "check: $AWS sts get-caller-identity" >&2
  exit 1
fi

if ! docker buildx version >/dev/null 2>&1; then
  echo "docker buildx is required to build for linux/$ARCHITECTURE" >&2
  exit 1
fi

# The gateway role has to exist before its ARN can be named in a resource
# policy; Lambda rejects add-permission for a principal it cannot resolve.
if ! "$AWS" iam get-role --role-name "${GATEWAY_ROLE_ARN##*/}" >/dev/null 2>&1; then
  echo "gateway role ${GATEWAY_ROLE_ARN} does not exist." >&2
  echo "It is created outside this repo and shared with create-gateway.sh." >&2
  exit 1
fi

# --- image ------------------------------------------------------------------

# Note the error is inspected rather than treated as "not found". A dropped
# connection also makes describe-repositories fail, and the naive version of
# this announces "creating ECR repository" for a repository that has existed
# for weeks -- misleading in exactly the moment you are reading output closely.
if ECR_ERR="$(ecr_ describe-repositories --repository-names "$ECR_REPO" 2>&1 >/dev/null)"; then
  note "ECR repository $ECR_REPO exists"
elif [[ "$ECR_ERR" == *RepositoryNotFoundException* ]]; then
  note "creating ECR repository $ECR_REPO"
  # Scanning on push is free and this image serves external traffic; there is
  # no reason to opt out of being told about a CVE in the base image.
  ecr_ create-repository --repository-name "$ECR_REPO" \
    --image-scanning-configuration scanOnPush=true --output json >/dev/null
else
  echo "could not query ECR repository $ECR_REPO:" >&2
  echo "$ECR_ERR" >&2
  exit 1
fi

note "logging docker in to $REGISTRY"
ecr_ get-login-password | docker login --username AWS --password-stdin "$REGISTRY"

note "building and pushing $IMAGE_URI (linux/$ARCHITECTURE)"
docker buildx build --platform "linux/$ARCHITECTURE" \
  --provenance=false \
  -t "$IMAGE_URI" --push "$BUILD_CONTEXT"

# --provenance=false above is not cosmetic: buildx otherwise pushes a multi-arch
# index with an attestation manifest, and Lambda rejects the result with
# "The image manifest, config or layer media type ... is not supported".

# --- execution role ---------------------------------------------------------

ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

if "$AWS" iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  note "role $ROLE_NAME exists"
else
  note "creating role $ROLE_NAME"
  "$AWS" iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
    --output json >/dev/null
  "$AWS" iam attach-role-policy --role-name "$ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
fi

# Written every run rather than only at creation, so changing KNOWLEDGE_BASE_ID
# above re-scopes the grant instead of silently leaving the old one in place.
# put-role-policy is an upsert.
note "granting bedrock:Retrieve on $KNOWLEDGE_BASE_ID"
RETRIEVE_POLICY="$SCRATCH/retrieve-policy.json"
KNOWLEDGE_BASE_ARN="$KNOWLEDGE_BASE_ARN" python3 - "$RETRIEVE_POLICY" <<'PY'
import json, os, sys

# Retrieve only, on one knowledge base ARN. Not bedrock:* and not "*": this
# role is what an IAM-based budget action would deny to stop upstream spend,
# and that is only a meaningful lever while the grant is this narrow.
json.dump({
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Action": "bedrock:Retrieve",
        "Resource": os.environ["KNOWLEDGE_BASE_ARN"],
    }],
}, open(sys.argv[1], "w"))
PY
"$AWS" iam put-role-policy --role-name "$ROLE_NAME" \
  --policy-name "$RETRIEVE_POLICY_NAME" \
  --policy-document "file://$RETRIEVE_POLICY"

# --- function ---------------------------------------------------------------

ENVIRONMENT="Variables={KNOWLEDGE_BASE_ID=$KNOWLEDGE_BASE_ID}"

if function_exists; then
  note "function $NAME exists, updating code"
  lambda_ update-function-code --function-name "$NAME" \
    --image-uri "$IMAGE_URI" --output json >/dev/null
  lambda_ wait function-updated-v2 --function-name "$NAME"

  note "updating configuration"
  lambda_ update-function-configuration --function-name "$NAME" \
    --timeout "$TIMEOUT_SECONDS" --memory-size "$MEMORY_MB" \
    --environment "$ENVIRONMENT" --output json >/dev/null
  lambda_ wait function-updated-v2 --function-name "$NAME"
else
  note "creating function $NAME"
  # IAM is eventually consistent: a role created moments ago is often not yet
  # visible to Lambda, which reports "The role defined for the function cannot
  # be assumed by Lambda". Retrying is the documented remedy.
  for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if lambda_ create-function --function-name "$NAME" \
        --package-type Image --code "ImageUri=$IMAGE_URI" \
        --role "$ROLE_ARN" \
        --architectures "$ARCHITECTURE" \
        --timeout "$TIMEOUT_SECONDS" --memory-size "$MEMORY_MB" \
        --environment "$ENVIRONMENT" \
        --description "MCP server over SFOE energy publications" \
        --output json >/dev/null 2>"$SCRATCH/create.err"; then
      break
    fi
    if ! grep -q "cannot be assumed by Lambda" "$SCRATCH/create.err"; then
      cat "$SCRATCH/create.err" >&2
      exit 1
    fi
    [[ $attempt -lt 10 ]] || { echo "role never became assumable" >&2; exit 1; }
    note "waiting for IAM role to propagate (attempt $attempt)"
    sleep 5
  done
  lambda_ wait function-active-v2 --function-name "$NAME"
fi

# --- concurrency cap ---------------------------------------------------------

note "capping reserved concurrency at $RESERVED_CONCURRENCY"
lambda_ put-function-concurrency --function-name "$NAME" \
  --reserved-concurrent-executions "$RESERVED_CONCURRENCY" --output json >/dev/null

# --- no function URL ---------------------------------------------------------

# The gateway invokes this function through the Lambda Invoke API, so there is
# nothing for a URL to serve. Any URL left over from the previous design is a
# second, HTTP-reachable entry point that bypasses the gateway's Cognito
# authorizer, so it is deleted rather than left switched off.
if [[ -n "$(function_url)" ]]; then
  note "deleting the obsolete function URL (the gateway no longer uses HTTP)"
  lambda_ delete-function-url-config --function-name "$NAME" >/dev/null
fi

# Three dead grants from three superseded designs: anonymous access, CloudFront
# in front of the URL, and the gateway invoking the URL directly. Each would be
# a way in that skips the authorizer, so none is left to rot.
#
# Matched on the exact Sid rather than with `grep -q`. Substring matching looks
# equivalent and is not: "AllowAgentCoreGateway" is a prefix of the statement
# added below, so the naive version finds it on the second run and then fails
# trying to remove a statement that does not exist.
for STALE in FunctionURLAllowPublicAccess AllowCloudFrontServicePrincipal \
             AllowAgentCoreGateway; do
  if has_statement "$STALE"; then
    note "removing obsolete invoke permission $STALE"
    lambda_ remove-permission --function-name "$NAME" --statement-id "$STALE"
  fi
done

# The gateway's role is the only principal that may invoke this function.
#
# Resource-side grant. Within one account this alone should be enough -- IAM
# evaluates identity-based and resource-based grants as an OR -- and the
# identity-side grant below is belt and braces rather than a proven necessity.
if ! has_statement AllowAgentCoreGatewayInvoke; then
  note "granting the gateway role permission to invoke the function"
  lambda_ add-permission --function-name "$NAME" \
    --statement-id AllowAgentCoreGatewayInvoke \
    --action lambda:InvokeFunction \
    --principal "$GATEWAY_ROLE_ARN" --output json >/dev/null
fi

# Identity-side grant. This edits a role owned outside this repo, so it is
# scoped as narrowly as it can be: one action, on this one function, nothing
# else. put-role-policy is an upsert and the policy name is specific to this
# function, so re-running cannot disturb the role's other permissions.
note "granting the gateway role identity-side invoke on ${NAME}"
"$AWS" iam put-role-policy \
  --role-name "${GATEWAY_ROLE_ARN##*/}" \
  --policy-name "${NAME}-invoke" \
  --policy-document "$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "InvokeMcpServerFunction",
      "Effect": "Allow",
      "Action": "lambda:InvokeFunction",
      "Resource": "arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:${NAME}"
    }
  ]
}
JSON
)"

# --- verify -----------------------------------------------------------------

# Invoked exactly the way the gateway will invoke it: the event is the tool's
# arguments, and the tool name arrives in the client context under the
# target-name prefix the gateway adds. That exercises the image, the handler's
# name-stripping and dispatch, argument validation, and the execution role's
# bedrock:Retrieve grant -- a real tool call, not just a handshake.
#
# The prefix used here is deliberately not $TARGET_NAME from create-gateway.sh:
# the handler must strip whatever prefix it is given, so a made-up one is the
# more honest test.
note "invoking $NAME the way the gateway does (search_energy_knowledge)"
RESPONSE="$SCRATCH/response.json"

CLIENT_CONTEXT="$(python3 -c '
import base64, json
print(base64.b64encode(json.dumps({"custom": {
    "bedrockAgentCoreMessageVersion": "1.0",
    "bedrockAgentCoreToolName": "deploy-check___search_energy_knowledge",
}}).encode()).decode())')"

# --cli-binary-format raw-in-base64-out is not optional here. --payload is a
# blob, and aws-cli v2 defaults to reading blobs as base64, so the JSON below
# is rejected outright with `Invalid base64`. The flag only changes how blob
# INPUTS are read; --client-context is a plain string parameter and must still
# be base64-encoded by hand, which is what the block above does.
lambda_ invoke --function-name "$NAME" \
  --cli-binary-format raw-in-base64-out \
  --payload '{"query": "Wasserkraft", "max_results": 1}' \
  --client-context "$CLIENT_CONTEXT" \
  --cli-read-timeout 120 \
  "$RESPONSE" --output json >/dev/null

python3 - "$RESPONSE" <<'PY'
import json, sys

payload = json.load(open(sys.argv[1]))
if isinstance(payload, dict) and "errorMessage" in payload:
    sys.exit(f"  function raised: {payload['errorMessage']}")

results = payload.get("results") if isinstance(payload, dict) else None
if not results:
    sys.exit(f"  tool returned no results: {json.dumps(payload)[:400]}")
print(f"  tool call OK: {len(results)} passage(s), "
      f"{len(payload.get('sources') or {})} source(s)")
PY

cat <<SUMMARY

done.
  function     : arn:aws:lambda:$REGION:$ACCOUNT_ID:function:$NAME
  reached by   : the Lambda Invoke API -- no URL, no HTTP endpoint
  invokers     : $GATEWAY_ROLE_ARN, and nobody else
  knowledge    : $KNOWLEDGE_BASE_ID via bedrock:Retrieve on the execution role
  image        : $IMAGE_URI ($ARCHITECTURE)
  concurrency  : $RESERVED_CONCURRENCY simultaneous requests; the rest get 429
  memory       : $MEMORY_MB MB (provisional -- check REPORT log lines for
                 Max Memory Used and Init Duration, then tune)
  logs         : $AWS logs tail /aws/lambda/$NAME --follow --region $REGION

This function is not addressable from the internet and is not the endpoint to
hand out. The gateway in front of it is; run the next step to point it here:

  ./infra/create-gateway.sh

  kill switch : ./infra/deploy-lambda.sh --pause
  teardown    : ./infra/deploy-lambda.sh --destroy
SUMMARY
