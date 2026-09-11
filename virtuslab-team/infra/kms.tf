# ---------------------------------------------------------------------------
# Customer-managed key for buckets Lambda must read.
#
# The AWS-managed aws/s3 key cannot be used here. Its key policy grants
# {"AWS": "*"} conditioned on kms:CallerAccount and kms:ViaService, which
# covers IAM principals in the account but NOT service principals - and the
# policy of an AWS-managed key is immutable, so lambda.amazonaws.com can never
# be added to it.
#
# Lambda fetches a deployment package as the service principal, so with
# aws/s3 encryption CreateFunction fails with:
#
#   Lambda is unable to access the specified S3 object. Verify that your
#   bucket policy grants the Lambda service principal s3:GetObjectVersion
#   on the object, with aws:SourceAccount scoped to your account.
#
# The message names only the bucket policy; the KMS grant is the other half
# and the part that cannot be satisfied with an AWS-managed key.
#
# Dropping back to AES256 is not an option: the BFE org resource control
# policy p-02xqecd89i denies any PutObject that is not KMS-encrypted.
# ---------------------------------------------------------------------------

locals {
  lambda_readable_buckets = {
    for key, cfg in var.buckets : key => cfg if try(cfg.lambda_readable, false)
  }

  # Only worth a key when something actually needs it and we are on KMS.
  create_artifact_key = var.bucket_sse_algorithm == "aws:kms" && length(local.lambda_readable_buckets) > 0

  log_delivery_buckets = {
    for key, cfg in var.buckets : key => cfg if try(cfg.log_delivery_target, false)
  }

  create_log_delivery_key = var.bucket_sse_algorithm == "aws:kms" && length(local.log_delivery_buckets) > 0
}

# ---------------------------------------------------------------------------
# Customer-managed key for buckets CloudWatch Logs delivers into.
#
# The same lesson as the artifacts key above, with a different service
# principal and a worse failure mode. AWS documents it plainly:
#
#   If you choose SSE-KMS, you must use a customer managed key, because using
#   an AWS managed key is not supported for this scenario. If you set up
#   encryption using an AWS managed key, the logs will be delivered in an
#   unreadable format.
#
# Not AccessDenied. Objects that exist, look delivered, and are garbage. The
# only check that catches it is gunzipping one and seeing whether it is JSON.
#
# AWS also warns that a bucket with SSE-KMS and a Bucket Key enabled makes the
# customer-managed key policy "no longer work as expected for all requests",
# which is why storage.tf turns the bucket key off for these buckets.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "log_delivery_key" {
  count = local.create_log_delivery_key ? 1 : 0

  # Without this the key becomes unmanageable by anyone, including us.
  statement {
    sid       = "EnableIAMUserPermissions"
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:${data.aws_partition.current.partition}:iam::${local.account_id}:root"]
    }
  }

  statement {
    sid    = "AllowLogsDeliveryToUseTheKey"
    effect = "Allow"

    # Verbatim from the AWS vended-logs documentation. ReEncrypt* and
    # GenerateDataKey* look generous for a write-only path; they are what the
    # service asks for, and trimming them is how the archive silently stops
    # filling.
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:DescribeKey",
    ]
    resources = ["*"]

    principals {
      type        = "Service"
      identifiers = ["delivery.logs.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }

    # The CloudWatch Logs service ARN, not the gateway ARN. The gateway is the
    # log source; the principal writing the object is CloudWatch Logs.
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${local.account_id}:*"]
    }
  }

  # Whoever reads the corpus back needs decrypt. Scoped by ViaService so the
  # key cannot be used for anything except S3.
  statement {
    sid    = "AllowAccountToReadTheCorpus"
    effect = "Allow"
    actions = [
      "kms:Decrypt",
      "kms:DescribeKey",
    ]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:${data.aws_partition.current.partition}:iam::${local.account_id}:root"]
    }

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.aws_region}.amazonaws.com"]
    }
  }
}

resource "aws_kms_key" "log_delivery" {
  count = local.create_log_delivery_key ? 1 : 0

  description             = "${local.name_prefix} gateway question archive - writable by the CloudWatch Logs delivery service principal"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy                  = data.aws_iam_policy_document.log_delivery_key[0].json
}

resource "aws_kms_alias" "log_delivery" {
  count = local.create_log_delivery_key ? 1 : 0

  name          = "alias/${local.name_prefix}-log-delivery"
  target_key_id = aws_kms_key.log_delivery[0].key_id
}

data "aws_iam_policy_document" "artifact_key" {
  count = local.create_artifact_key ? 1 : 0

  # Without this the key becomes unmanageable by anyone, including us.
  statement {
    sid       = "EnableIAMUserPermissions"
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:${data.aws_partition.current.partition}:iam::${local.account_id}:root"]
    }
  }

  statement {
    sid    = "AllowLambdaServiceToDecryptPackages"
    effect = "Allow"
    actions = [
      "kms:Decrypt",
      "kms:DescribeKey",
    ]
    resources = ["*"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    # Confused-deputy guard, and exactly what the Lambda error asks for.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_kms_key" "artifacts" {
  count = local.create_artifact_key ? 1 : 0

  description             = "${local.name_prefix} Lambda deployment packages - readable by the Lambda service principal"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy                  = data.aws_iam_policy_document.artifact_key[0].json
}

resource "aws_kms_alias" "artifacts" {
  count = local.create_artifact_key ? 1 : 0

  name          = "alias/${local.name_prefix}-artifacts"
  target_key_id = aws_kms_key.artifacts[0].key_id
}
