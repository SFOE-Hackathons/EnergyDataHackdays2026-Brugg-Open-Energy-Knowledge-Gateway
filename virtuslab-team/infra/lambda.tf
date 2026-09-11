# MCP tool Lambda. Opt-in: it bills, and the account has ~$68 of budget left.
# The deployment package is built and uploaded outside Terraform - see README.

resource "aws_lambda_layer_version" "dependencies" {
  count = var.enable_lambda && var.lambda_layer_key != null ? 1 : 0

  layer_name          = "${local.name_prefix}-deps"
  description         = "Python dependencies for the MCP tool Lambda"
  s3_bucket           = var.lambda_artifact_bucket
  s3_key              = var.lambda_layer_key
  compatible_runtimes = ["python3.13"]
}

resource "aws_cloudwatch_log_group" "lambda" {
  count = var.enable_lambda ? 1 : 0

  # Created explicitly so retention applies. Left to Lambda, the group is
  # created on first invocation and never expires.
  name              = "/aws/lambda/${local.name_prefix}-mcp-tools"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "mcp_tools" {
  count = var.enable_lambda ? 1 : 0

  function_name = "${local.name_prefix}-mcp-tools"
  description   = "Tool backend behind the AgentCore gateway"
  role          = aws_iam_role.lambda.arn

  s3_bucket = var.lambda_artifact_bucket
  s3_key    = var.lambda_package_key

  runtime       = "python3.13"
  handler       = "lambda_function.lambda_handler"
  architectures = ["x86_64"]
  timeout       = 90
  memory_size   = 128

  layers = var.lambda_layer_key != null ? [aws_lambda_layer_version.dependencies[0].arn] : []

  environment {
    variables = {
      BEDROCK_KB_ID             = var.knowledge_base_id
      BEDROCK_KB_DATA_SOURCE_ID = var.knowledge_base_data_source_id
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda]

  lifecycle {
    precondition {
      condition     = var.lambda_artifact_bucket != null && var.lambda_package_key != null
      error_message = "enable_lambda requires lambda_artifact_bucket and lambda_package_key."
    }
  }
}
