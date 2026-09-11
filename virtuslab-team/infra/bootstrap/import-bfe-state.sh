#!/usr/bin/env bash
#
# One-time: populate the `bfe` workspace state with the resources that already
# exist in the BFE account.
#
# State used to live in the VirtusLab sandbox account. Moving it into BFE meant
# either migrating (which needs VirtusLab SSO on every machine) or re-importing
# (which needs only the BFE credentials the whole team already has). This does
# the latter. Nothing is created, changed or deleted - import only writes state.
#
# Idempotent: anything already in state is skipped, so a partial run is safe to
# repeat.
#
# Usage:  ./bootstrap/import-bfe-state.sh

set -uo pipefail

ACCOUNT=542202863496
PREFIX=vl-bfe-kg-bfe
KB_POLICY="arn:aws:iam::${ACCOUNT}:policy/${PREFIX}-kb-read"
POOL=eu-central-1_DOer30qrA
CLIENT=3bb2sgsr2urspuke653u3otrcq
DOMAIN=${PREFIX}-${ACCOUNT}
GATEWAY=${PREFIX}-gateway-bpyoqng3qg

terraform workspace select -or-create bfe >/dev/null || exit 1
echo "workspace: $(terraform workspace show)"
echo

IN_STATE="$(terraform state list 2>/dev/null)"
ok=0; skip=0; fail=0

imp() { # imp <address> <id>
  if grep -qxF "$1" <<<"$IN_STATE"; then
    printf "  skip   %s\n" "$1"; skip=$((skip+1)); return
  fi
  if out=$(terraform import -input=false -var-file=envs/bfe.tfvars "$1" "$2" 2>&1); then
    printf "  ok     %s\n" "$1"; ok=$((ok+1))
  else
    printf "  FAIL   %s\n         %s\n" "$1" "$(grep -m1 -E 'Error|error' <<<"$out" | head -c 160)"
    fail=$((fail+1))
  fi
}

for b in artifacts graph; do
  bucket="${PREFIX}-${b}"
  imp "aws_s3_bucket.this[\"${b}\"]"                                  "$bucket"
  imp "aws_s3_bucket_versioning.this[\"${b}\"]"                       "$bucket"
  imp "aws_s3_bucket_server_side_encryption_configuration.this[\"${b}\"]" "$bucket"
  imp "aws_s3_bucket_public_access_block.this[\"${b}\"]"              "$bucket"
  imp "aws_s3_bucket_ownership_controls.this[\"${b}\"]"               "$bucket"
done

imp "aws_iam_policy.knowledge_base_read"  "$KB_POLICY"
imp "aws_iam_role.gateway"                "${PREFIX}-gateway"
imp "aws_iam_role.agent_runtime"          "${PREFIX}-runtime"
imp "aws_iam_role.lambda"                 "${PREFIX}-lambda"

imp "aws_iam_role_policy.agent_runtime"        "${PREFIX}-runtime:runtime-permissions"
imp "aws_iam_role_policy.lambda_read_buckets[0]" "${PREFIX}-lambda:read-owned-buckets"

imp "aws_iam_role_policy_attachment.gateway_kb_read"       "${PREFIX}-gateway/${KB_POLICY}"
imp "aws_iam_role_policy_attachment.agent_runtime_kb_read" "${PREFIX}-runtime/${KB_POLICY}"
imp "aws_iam_role_policy_attachment.lambda_kb_read"        "${PREFIX}-lambda/${KB_POLICY}"
imp "aws_iam_role_policy_attachment.lambda_basic_execution" \
    "${PREFIX}-lambda/arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"

imp "aws_cognito_user_pool.this[0]"          "$POOL"
imp "aws_cognito_user_pool_client.this[0]"   "${POOL}/${CLIENT}"
imp "aws_cognito_user_pool_domain.this[0]"   "$DOMAIN"
imp "aws_cognito_resource_server.this[0]"    "${POOL}|bfe-knowledge-gateway"

imp "aws_bedrockagentcore_gateway.this[0]"   "$GATEWAY"

echo
echo "imported=$ok skipped=$skip failed=$fail"
echo "Next: make plan ENV=bfe   - expect no creates for anything above."
[ "$fail" -eq 0 ]
