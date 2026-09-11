# Buckets this stack owns. Every one gets versioning, encryption and a full
# public-access block - none of the three buckets in the sandbox has versioning
# today, which makes knowledge_graph.ttl and the source PDFs unrecoverable if
# they are overwritten.

resource "aws_s3_bucket" "this" {
  for_each = local.bucket_names

  bucket = each.value

  # Explicit, and load-bearing. Terraform cannot delete a bucket that still has
  # objects in it unless this is true, so the adopted buckets holding the KB
  # corpus and the generated knowledge graph are protected by their own
  # contents: `terraform destroy` fails with BucketNotEmpty.
  #
  # An empty throwaway bucket in a test workspace still destroys cleanly, which
  # is what makes `ENV=test` a disposable environment. Do not set this to true
  # to force a destroy through - empty the bucket deliberately instead.
  force_destroy = false
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = var.bucket_sse_algorithm
      # Buckets Lambda must read get the customer-managed key; the rest get
      # whatever was configured, where null means the AWS-managed aws/s3 key.
      # Ignored entirely for AES256.
      kms_master_key_id = var.bucket_sse_algorithm == "aws:kms" ? local.bucket_kms_key_ids[each.key] : null
    }

    # AWS: "If the destination bucket has SSE-KMS and a Bucket Key enabled, the
    # attached customer managed KMS key policy no longer works as expected for
    # all requests." Our key policy is conditioned on aws:SourceAccount rather
    # than on encryption context, so it would probably survive - but "probably"
    # is how you end up with an archive full of unreadable objects. Turning it
    # off costs more KMS calls at $0.03 per 10,000, on a handful of objects an
    # hour.
    bucket_key_enabled = !try(var.buckets[each.key].log_delivery_target, false)
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id

  rule {
    # Disables ACLs entirely, so no aws_s3_bucket_acl resource is needed.
    object_ownership = "BucketOwnerEnforced"
  }
}

# A bucket has exactly ONE policy. Two aws_s3_bucket_policy resources on the
# same bucket overwrite each other on every apply, with no plan diff to show
# for it, so every capability contributes a statement to one document here
# rather than owning a policy of its own.
locals {
  policied_buckets = toset(concat(
    keys(local.lambda_readable_buckets),
    keys(local.log_delivery_buckets),
  ))
}

data "aws_iam_policy_document" "bucket" {
  for_each = local.policied_buckets

  # Lambda fetches a deployment package as the service principal, not as the
  # caller, so the bucket must grant lambda.amazonaws.com directly. This is the
  # half the AWS error message names; the KMS key policy in kms.tf is the other.
  dynamic "statement" {
    for_each = try(var.buckets[each.key].lambda_readable, false) ? [1] : []

    content {
      sid    = "AllowLambdaServiceToFetchPackages"
      effect = "Allow"

      principals {
        type        = "Service"
        identifiers = ["lambda.amazonaws.com"]
      }

      # GetObjectVersion as well as GetObject: these buckets are versioned, and
      # Lambda resolves the package by version id.
      actions = [
        "s3:GetObject",
        "s3:GetObjectVersion",
      ]

      resources = ["${aws_s3_bucket.this[each.key].arn}/*"]

      condition {
        test     = "StringEquals"
        variable = "aws:SourceAccount"
        values   = [local.account_id]
      }
    }
  }

  # CloudWatch Logs delivers vended logs as delivery.logs.amazonaws.com, its
  # own service principal - the same shape of problem as Lambda above. Note
  # aws:SourceArn is the LOGS service ARN, not the gateway ARN: the gateway is
  # the log source, but the caller writing the object is CloudWatch Logs.
  dynamic "statement" {
    for_each = try(var.buckets[each.key].log_delivery_target, false) ? [1] : []

    content {
      sid    = "AWSLogDeliveryAclCheck"
      effect = "Allow"

      principals {
        type        = "Service"
        identifiers = ["delivery.logs.amazonaws.com"]
      }

      # ListBucket is on AWS's own advice: without it delivery still works but
      # fills CloudTrail with AccessDenied noise.
      actions   = ["s3:GetBucketAcl", "s3:ListBucket"]
      resources = [aws_s3_bucket.this[each.key].arn]

      condition {
        test     = "StringEquals"
        variable = "aws:SourceAccount"
        values   = [local.account_id]
      }

      condition {
        test     = "ArnLike"
        variable = "aws:SourceArn"
        values   = ["arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${local.account_id}:*"]
      }
    }
  }

  dynamic "statement" {
    for_each = try(var.buckets[each.key].log_delivery_target, false) ? [1] : []

    content {
      sid    = "AWSLogDeliveryWrite"
      effect = "Allow"

      principals {
        type        = "Service"
        identifiers = ["delivery.logs.amazonaws.com"]
      }

      actions = ["s3:PutObject"]

      # AWSLogs/* rather than AWSLogs/<account>/*: with hive-compatible paths
      # the prefix becomes AWSLogs/aws-account-id=<account>/ and an
      # account-scoped prefix silently stops matching. aws:SourceAccount below
      # does the real scoping.
      resources = ["${aws_s3_bucket.this[each.key].arn}/AWSLogs/*"]

      condition {
        test     = "StringEquals"
        variable = "s3:x-amz-acl"
        # Accepted even though object_ownership is BucketOwnerEnforced:
        # bucket-owner-full-control is the one canned ACL S3 still allows with
        # ACLs disabled. Anything else fails AccessControlListNotSupported.
        values = ["bucket-owner-full-control"]
      }

      condition {
        test     = "StringEquals"
        variable = "aws:SourceAccount"
        values   = [local.account_id]
      }

      condition {
        test     = "ArnLike"
        variable = "aws:SourceArn"
        values   = ["arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${local.account_id}:*"]
      }
    }
  }
}

resource "aws_s3_bucket_policy" "this" {
  for_each = local.policied_buckets

  bucket = aws_s3_bucket.this[each.key].id
  policy = data.aws_iam_policy_document.bucket[each.key].json

  # A policy referring to a bucket whose public-access block is still settling
  # is rejected, so make the ordering explicit.
  depends_on = [aws_s3_bucket_public_access_block.this]
}

# A rename, not a replacement: the live policy on the artifacts bucket stays
# put. Without this Terraform destroys and recreates it, and for a moment the
# Lambda service principal cannot read its own deployment package.
moved {
  from = aws_s3_bucket_policy.lambda_readable
  to   = aws_s3_bucket_policy.this
}
