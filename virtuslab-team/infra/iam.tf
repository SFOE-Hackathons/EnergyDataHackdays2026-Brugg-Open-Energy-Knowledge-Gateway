# ---------------------------------------------------------------------------
# The roles that were painful to build by hand in the Console.
#
# IAM is free, so none of it is gated behind the enable_* toggles - only the
# compute that bills. Actions come from the two inventories already in this
# repo: kg_creation/AGENT_NOTES.md:160-167 and
# docs/connecting-to-bedrock-knowledge-base.md:275-289.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "agentcore_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }

    # Confused-deputy guard: only this account's AgentCore may assume the role.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

# Read-only access to BFE's knowledge base. The ARN is the one the API reports
# for the configured id, so it is impossible for this policy to point at a KB
# that does not exist - the lookup in data.tf fails first.
data "aws_iam_policy_document" "knowledge_base_read" {
  statement {
    sid    = "RetrieveFromKnowledgeBase"
    effect = "Allow"
    actions = [
      "bedrock:Retrieve",
      "bedrock:RetrieveAndGenerate",
      "bedrock:GetKnowledgeBase",
      "bedrock:ListDataSources",
      "bedrock:GetDataSource",
    ]
    resources = [local.kb_arn]
  }

  statement {
    sid       = "InvokeFoundationModels"
    effect    = "Allow"
    actions   = ["bedrock:InvokeModel"]
    resources = ["arn:${data.aws_partition.current.partition}:bedrock:*::foundation-model/*"]
  }

  # Only emitted when there is a source bucket to grant. The live sandbox policy
  # has "arn:aws:s3:::sfoe-data-energy-monitoring " with a trailing space, so its
  # bucket-level ListBucket silently never matches; deriving ARNs makes that
  # class of typo impossible.
  dynamic "statement" {
    for_each = length(local.kb_source_bucket_arns) > 0 ? [1] : []

    content {
      sid       = "ReadKnowledgeBaseSourceObjects"
      effect    = "Allow"
      actions   = ["s3:GetObject"]
      resources = local.kb_source_object_arns
    }
  }

  dynamic "statement" {
    for_each = length(local.kb_source_bucket_arns) > 0 ? [1] : []

    content {
      sid       = "ListKnowledgeBaseSourceBuckets"
      effect    = "Allow"
      actions   = ["s3:ListBucket"]
      resources = local.kb_source_bucket_arns
    }
  }
}

resource "aws_iam_policy" "knowledge_base_read" {
  name        = "${local.name_prefix}-kb-read"
  description = "Read-only access to Bedrock knowledge base ${var.knowledge_base_id}"
  policy      = data.aws_iam_policy_document.knowledge_base_read.json
}

# --- Gateway -----------------------------------------------------------------

resource "aws_iam_role" "gateway" {
  name               = "${local.name_prefix}-gateway"
  description        = "AgentCore Gateway: exposes the knowledge base as MCP tools"
  assume_role_policy = data.aws_iam_policy_document.agentcore_assume_role.json
}

resource "aws_iam_role_policy_attachment" "gateway_kb_read" {
  role       = aws_iam_role.gateway.name
  policy_arn = aws_iam_policy.knowledge_base_read.arn
}

# The gateway calls the target Lambda with its own role, so it needs invoke on
# whichever function the target points at - ours, or an external one.
data "aws_iam_policy_document" "gateway_invoke_lambda" {
  count = local.gateway_needs_invoke_policy ? 1 : 0

  statement {
    sid       = "InvokeGatewayTargetFunctions"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = local.gateway_invokable_lambda_arns
  }
}

resource "aws_iam_role_policy" "gateway_invoke_lambda" {
  count = local.gateway_needs_invoke_policy ? 1 : 0

  name   = "invoke-mcp-tools"
  role   = aws_iam_role.gateway.id
  policy = data.aws_iam_policy_document.gateway_invoke_lambda[0].json
}

# --- Agent runtime -----------------------------------------------------------

resource "aws_iam_role" "agent_runtime" {
  name               = "${local.name_prefix}-runtime"
  description        = "AgentCore Runtime: hosts the MCP server container"
  assume_role_policy = data.aws_iam_policy_document.agentcore_assume_role.json
}

resource "aws_iam_role_policy_attachment" "agent_runtime_kb_read" {
  role       = aws_iam_role.agent_runtime.name
  policy_arn = aws_iam_policy.knowledge_base_read.arn
}

data "aws_iam_policy_document" "agent_runtime" {
  statement {
    sid       = "EcrAuth"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "EcrPull"
    effect = "Allow"
    actions = [
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchCheckLayerAvailability",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:ecr:${var.aws_region}:${local.account_id}:repository/*",
    ]
  }

  statement {
    sid    = "WriteLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${local.account_id}:log-group:/aws/bedrock-agentcore/*",
    ]
  }
}

resource "aws_iam_role_policy" "agent_runtime" {
  name   = "runtime-permissions"
  role   = aws_iam_role.agent_runtime.id
  policy = data.aws_iam_policy_document.agent_runtime.json
}

# --- Lambda ------------------------------------------------------------------

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${local.name_prefix}-lambda"
  description        = "MCP tool Lambda execution role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "lambda_kb_read" {
  role       = aws_iam_role.lambda.name
  policy_arn = aws_iam_policy.knowledge_base_read.arn
}

data "aws_iam_policy_document" "lambda_read_buckets" {
  count = length(local.owned_bucket_arns) > 0 ? 1 : 0

  statement {
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = local.owned_bucket_object_arns
  }

  statement {
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = local.owned_bucket_arns
  }

  # With KMS bucket encryption, s3:GetObject alone is not enough - S3 decrypts
  # on the caller's behalf, so the caller needs the KMS grant too or every read
  # fails at runtime with AccessDenied. Scoped by ViaService so the role cannot
  # use the key for anything but S3.
  dynamic "statement" {
    for_each = var.bucket_sse_algorithm == "aws:kms" ? [1] : []

    content {
      sid    = "UseBucketEncryptionKey"
      effect = "Allow"
      actions = [
        "kms:Decrypt",
        "kms:GenerateDataKey",
        "kms:DescribeKey",
      ]
      resources = [coalesce(var.bucket_kms_key_id, "*")]

      condition {
        test     = "StringEquals"
        variable = "kms:ViaService"
        values   = ["s3.${var.aws_region}.amazonaws.com"]
      }
    }
  }
}

resource "aws_iam_role_policy" "lambda_read_buckets" {
  count = length(local.owned_bucket_arns) > 0 ? 1 : 0

  name   = "read-owned-buckets"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda_read_buckets[0].json
}
