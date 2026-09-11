#!/usr/bin/env bash
#
# Create the S3 bucket that holds Terraform state for this project.
#
# Idempotent: every step checks before it writes, so re-running is a no-op.
#
# Encryption is SSE-KMS with the free AWS-managed aws/s3 key, not AES256. The
# BFE org enforces a resource control policy (p-02xqecd89i) that denies any
# PutObject which is not KMS-encrypted, and Terraform state writes - including
# the .tflock lock object - are PutObject. AES256 here means every apply fails
# with AccessDenied and no IAM change can fix it.
# Deliberately NOT Terraform - the state backend cannot be managed by the state
# it stores. Deliberately no DynamoDB table either; locking is the S3-native
# lockfile (backend `use_lockfile = true`), GA since Terraform 1.11.
#
# Usage:  ./bootstrap/bootstrap-state.sh [aws-profile] [region]

set -euo pipefail

PROFILE="${1:-${AWS_PROFILE:-bfe}}"
REGION="${2:-${AWS_REGION:-eu-central-1}}"
PREFIX="${3:-vl-bfe-kg-tfstate}"

aws_() { aws --profile "$PROFILE" --region "$REGION" --no-cli-pager "$@"; }

say()  { printf '  %s\n' "$*"; }
ok()   { printf '  \033[32mok\033[0m   %s\n' "$*"; }
made() { printf '  \033[33mset\033[0m  %s\n' "$*"; }

if ! ACCOUNT_ID="$(aws_ sts get-caller-identity --query Account --output text 2>/dev/null)"; then
  echo "error: cannot authenticate with profile '$PROFILE'." >&2
  echo "       run: aws sso login --profile $PROFILE" >&2
  exit 1
fi

BUCKET="${PREFIX}-${ACCOUNT_ID}"

echo "Terraform state bootstrap"
say "profile  $PROFILE"
say "account  $ACCOUNT_ID"
say "region   $REGION"
say "bucket   $BUCKET"
echo

# --- bucket ------------------------------------------------------------------
if aws_ s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  ok "bucket exists"
else
  if [ "$REGION" = "us-east-1" ]; then
    # us-east-1 rejects a LocationConstraint.
    aws_ s3api create-bucket --bucket "$BUCKET" >/dev/null
  else
    aws_ s3api create-bucket --bucket "$BUCKET" \
      --create-bucket-configuration "LocationConstraint=$REGION" >/dev/null
  fi
  made "bucket created"
fi

# --- versioning: this is the rollback mechanism ------------------------------
if [ "$(aws_ s3api get-bucket-versioning --bucket "$BUCKET" \
        --query 'Status' --output text 2>/dev/null)" = "Enabled" ]; then
  ok "versioning enabled"
else
  aws_ s3api put-bucket-versioning --bucket "$BUCKET" \
    --versioning-configuration Status=Enabled
  made "versioning enabled"
fi

# --- encryption --------------------------------------------------------------
# Check the ALGORITHM, not merely that some config exists: S3 now applies
# AES256 to every new bucket automatically, so an existence check silently
# leaves the bucket on AES256 - which the BFE RCP then denies every write to.
CUR_SSE="$(aws_ s3api get-bucket-encryption --bucket "$BUCKET" \
  --query 'ServerSideEncryptionConfiguration.Rules[0].ApplyServerSideEncryptionByDefault.SSEAlgorithm' \
  --output text 2>/dev/null || echo none)"
if [ "$CUR_SSE" = "aws:kms" ]; then
  ok "encryption is SSE-KMS"
else
  aws_ s3api put-bucket-encryption --bucket "$BUCKET" \
    --server-side-encryption-configuration '{
      "Rules": [{
        "ApplyServerSideEncryptionByDefault": {
          "SSEAlgorithm": "aws:kms",
          "KMSMasterKeyID": "alias/aws/s3"
        },
        "BucketKeyEnabled": true
      }]
    }'
  made "encryption set to SSE-KMS (was: $CUR_SSE)"
fi

# --- public access block -----------------------------------------------------
if [ "$(aws_ s3api get-public-access-block --bucket "$BUCKET" \
        --query 'PublicAccessBlockConfiguration.BlockPublicAcls' \
        --output text 2>/dev/null)" = "True" ]; then
  ok "public access blocked"
else
  aws_ s3api put-public-access-block --bucket "$BUCKET" \
    --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
  made "public access blocked"
fi

# --- TLS-only policy ---------------------------------------------------------
if aws_ s3api get-bucket-policy --bucket "$BUCKET" >/dev/null 2>&1; then
  ok "bucket policy present"
else
  aws_ s3api put-bucket-policy --bucket "$BUCKET" --policy "$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "DenyInsecureTransport",
    "Effect": "Deny",
    "Principal": "*",
    "Action": "s3:*",
    "Resource": [
      "arn:aws:s3:::${BUCKET}",
      "arn:aws:s3:::${BUCKET}/*"
    ],
    "Condition": {"Bool": {"aws:SecureTransport": "false"}}
  }]
}
JSON
)"
  made "TLS-only policy applied"
fi

# --- lifecycle: keep rollback history bounded --------------------------------
if aws_ s3api get-bucket-lifecycle-configuration --bucket "$BUCKET" >/dev/null 2>&1; then
  ok "lifecycle rule present"
else
  aws_ s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" \
    --lifecycle-configuration '{
      "Rules": [{
        "ID": "expire-noncurrent-state",
        "Status": "Enabled",
        "Filter": {"Prefix": ""},
        "NoncurrentVersionExpiration": {"NoncurrentDays": 90},
        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7}
      }]
    }' >/dev/null
  made "lifecycle rule applied"
fi

echo
echo "Done. versions.tf already points at this bucket:"
echo
echo "    bucket       = \"$BUCKET\""
echo "    key          = \"bfe-knowledge-gateway/terraform.tfstate\""
echo "    region       = \"$REGION\""
echo "    kms_key_id   = \"alias/aws/s3\""
echo "    use_lockfile = true"
echo
echo "Next:  make init ENV=sandbox"
