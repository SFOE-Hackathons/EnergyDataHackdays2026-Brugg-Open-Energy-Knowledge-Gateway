# ---------------------------------------------------------------------------
# Gateway observability: what people actually ask the corpus.
#
# The question text lives in exactly one place. A gateway APPLICATION_LOGS
# record carries body.requestBody, which is the whole MCP JSON-RPC envelope
# including the tool arguments:
#
#   {id=1, jsonrpc=2.0, method=tools/call,
#    params={name=vl-KB-bfe-public___Retrieve, arguments={query=...}}}
#
# The TRACES spans do NOT carry arguments - only tool.name, latency_ms, status
# and error codes - so traces are the wrong tool for this job, and in this
# account they are impossible anyway: SCP p-bvj3x4lc denies xray:* outright, so
# CloudWatch Transaction Search cannot be turned on. See RUNBOOK.md.
#
# The ops half needs nothing from this file. AWS/Bedrock-AgentCore already
# publishes Invocations, Latency, Duration, Errors, Throttles and
# InboundAuthorizationSuccess/Failure with no configuration at all, which is
# why the dashboard's metric widgets keep working even if delivery is denied.
#
# Only this gateway is observed. Observing another one is a second delivery
# source pointing at its ARN - a delivery source only reads, so it does not
# require Terraform to own the gateway.
# ---------------------------------------------------------------------------

locals {
  # Derived from VARIABLES only, like gateway_needs_invoke_policy: a count that
  # reads a resource attribute is unknown until apply and breaks import.
  #
  # Chained on enable_agentcore so `make down` takes the delivery away with the
  # gateway it observes - a delivery source pointing at a gateway that no
  # longer exists fails the apply.
  gateway_logs_enabled = var.enable_agentcore && var.enable_gateway_observability

  questions_archive_enabled = local.gateway_logs_enabled && length(local.log_delivery_buckets) > 0
}

# --- Destination 1: CloudWatch Logs ------------------------------------------

# The /aws/vendedlogs/ prefix is load-bearing, not cosmetic. CloudWatch Logs
# keeps an account-level resource policy that lets delivery.logs.amazonaws.com
# write to groups under that prefix. A group named anything else needs a
# hand-written resource policy, against a cap of ten per region.
#
# Created explicitly rather than left to AgentCore so that retention applies -
# the same reason aws_cloudwatch_log_group.lambda exists.
resource "aws_cloudwatch_log_group" "gateway" {
  count = local.gateway_logs_enabled ? 1 : 0

  name              = "/aws/vendedlogs/bedrock-agentcore/gateway/${local.name_prefix}"
  retention_in_days = var.log_retention_days
}

# One source, several destinations: CreateDelivery documents that "you can
# configure a single delivery source to send logs to multiple destinations by
# creating multiple deliveries", so CWL and S3 below share this one.
resource "aws_cloudwatch_log_delivery_source" "gateway" {
  count = local.gateway_logs_enabled ? 1 : 0

  name         = "${local.name_prefix}-gateway-app-logs"
  log_type     = "APPLICATION_LOGS"
  resource_arn = aws_bedrockagentcore_gateway.this[0].gateway_arn
}

resource "aws_cloudwatch_log_delivery_destination" "gateway_cwl" {
  count = local.gateway_logs_enabled ? 1 : 0

  name          = "${local.name_prefix}-gateway-cwl"
  output_format = "json"

  delivery_destination_configuration {
    destination_resource_arn = aws_cloudwatch_log_group.gateway[0].arn
  }
}

resource "aws_cloudwatch_log_delivery" "gateway_cwl" {
  count = local.gateway_logs_enabled ? 1 : 0

  delivery_source_name     = aws_cloudwatch_log_delivery_source.gateway[0].name
  delivery_destination_arn = aws_cloudwatch_log_delivery_destination.gateway_cwl[0].arn

  # record_fields is deliberately unset. Unset means every field; naming fields
  # here is how body.requestBody - the only place the question appears - gets
  # silently dropped.
}

# --- Destination 2: S3, the durable question corpus --------------------------
#
# CloudWatch retention is measured in days; the corpus needs to outlive the
# event. Read kms.tf before changing anything about this bucket's encryption:
# under an AWS-managed key AWS delivers these objects "in an unreadable format"
# rather than failing, which is a far worse outcome than AccessDenied.

resource "aws_cloudwatch_log_delivery_destination" "gateway_s3" {
  for_each = local.questions_archive_enabled ? local.log_delivery_buckets : {}

  name          = "${local.name_prefix}-gateway-s3-${each.key}"
  output_format = "json"

  delivery_destination_configuration {
    destination_resource_arn = aws_s3_bucket.this[each.key].arn
  }
}

resource "aws_cloudwatch_log_delivery" "gateway_s3" {
  for_each = local.questions_archive_enabled ? local.log_delivery_buckets : {}

  delivery_source_name     = aws_cloudwatch_log_delivery_source.gateway[0].name
  delivery_destination_arn = aws_cloudwatch_log_delivery_destination.gateway_s3[each.key].arn

  s3_delivery_configuration {
    # Left false on purpose. Hive-compatible paths rewrite the prefix from
    # AWSLogs/<account>/ to AWSLogs/aws-account-id=<account>/, which stops
    # matching a bucket policy scoped to the documented prefix - and the
    # objects then fail to write with nothing surfaced anywhere. If Athena
    # partitioning is wanted later, widen the bucket policy in the same commit.
    enable_hive_compatible_path = false
  }

  # CreateDelivery validates the destination, so a bucket policy that has not
  # landed yet gives a first-apply failure that then succeeds on retry.
  depends_on = [aws_s3_bucket_policy.this]
}

# --- Reading the questions back ----------------------------------------------
#
# body.requestBody is NOT JSON. It is a Java-map-style string - unquoted keys,
# unquoted values, "=" instead of ":" - so `parse ... as json` does nothing
# with it and the extraction has to be a regex.
#
# The regex is structural rather than field-name-based, because the KB
# connector's argument name is not documented and differs per target. The
# string always ends `arguments={...}}}`: three closing braces for arguments,
# params and the envelope. A GREEDY .* therefore captures the whole argument
# map intact, nested braces and all. Making it non-greedy truncates every
# multi-field argument map - do not "simplify" it.
#
# CloudWatch Logs Insights uses (?<name>...), not Python's (?P<name>...).

resource "aws_cloudwatch_query_definition" "questions_asked" {
  count = local.gateway_logs_enabled ? 1 : 0

  # The slash files these under a folder in the console's Queries panel.
  name            = "${local.name_prefix}/questions-asked"
  log_group_names = [aws_cloudwatch_log_group.gateway[0].name]

  query_string = <<-EOT
    fields @timestamp, `body.requestBody` as req, trace_id, request_id
    | filter ispresent(req) and req like "method=tools/call"
    | parse req /name=(?<tool>[^,}]+)/
    | parse req /arguments=\{(?<args>.*)\}\}\}/
    | display @timestamp, tool, args, trace_id
    | sort @timestamp desc
    | limit 200
  EOT
}

resource "aws_cloudwatch_query_definition" "questions_ranked" {
  count = local.gateway_logs_enabled ? 1 : 0

  name            = "${local.name_prefix}/questions-ranked"
  log_group_names = [aws_cloudwatch_log_group.gateway[0].name]

  query_string = <<-EOT
    fields `body.requestBody` as req
    | filter ispresent(req) and req like "method=tools/call"
    | parse req /name=(?<tool>[^,}]+)/
    | parse req /arguments=\{(?<args>.*)\}\}\}/
    | stats count(*) as asked, earliest(@timestamp) as first_seen, latest(@timestamp) as last_seen by tool, args
    | sort asked desc
    | limit 100
  EOT
}

resource "aws_cloudwatch_query_definition" "gateway_failures" {
  count = local.gateway_logs_enabled ? 1 : 0

  name            = "${local.name_prefix}/failures"
  log_group_names = [aws_cloudwatch_log_group.gateway[0].name]

  # Two different error signals, and they mean different things: body.isError
  # is the gateway-level flag, while a TOOL that failed reports isError=true
  # inside responseBody. "rror" catches Error and error without a case flag.
  query_string = <<-EOT
    fields @timestamp, `body.log` as log, `body.isError` as gateway_error,
           `body.responseBody` as resp, trace_id, span_id, request_id
    | filter ispresent(gateway_error) or resp like "isError=true" or log like "rror"
    | sort @timestamp desc
    | limit 100
  EOT
}

# --- Dashboard ---------------------------------------------------------------
#
# The metric widgets read AWS/Bedrock-AgentCore, which needs no delivery at
# all, so the ops half of this dashboard survives an SCP denial on
# logs:CreateDelivery. Only the bottom widget depends on the log group.
resource "aws_cloudwatch_dashboard" "gateway" {
  count = local.gateway_logs_enabled ? 1 : 0

  dashboard_name = "${local.name_prefix}-gateway"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Requests by MCP method"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = 300
          metrics = [
            ["AWS/Bedrock-AgentCore", "Invocations", "Resource", aws_bedrockagentcore_gateway.this[0].gateway_arn, "Method", "tools/call"],
            ["...", "Method", "tools/list"],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Errors, throttles and rejected auth"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = 300
          metrics = [
            ["AWS/Bedrock-AgentCore", "UserErrors", "Resource", aws_bedrockagentcore_gateway.this[0].gateway_arn],
            ["...", "SystemErrors", "Resource", aws_bedrockagentcore_gateway.this[0].gateway_arn],
            ["...", "Throttles", "Resource", aws_bedrockagentcore_gateway.this[0].gateway_arn],
            ["...", "InboundAuthorizationFailure", "Resource", aws_bedrockagentcore_gateway.this[0].gateway_arn],
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Latency, and how much of it is the target"
          region = var.aws_region
          view   = "timeSeries"
          period = 300
          metrics = [
            ["AWS/Bedrock-AgentCore", "Latency", "Resource", aws_bedrockagentcore_gateway.this[0].gateway_arn, { stat = "p50" }],
            ["...", { stat = "p90" }],
            ["...", { stat = "p99" }],
          ]
        }
      },
      {
        type   = "log"
        x      = 0
        y      = 12
        width  = 24
        height = 12
        properties = {
          title  = "What people are asking"
          region = var.aws_region
          view   = "table"
          query  = <<-EOT
            SOURCE '${aws_cloudwatch_log_group.gateway[0].name}'
            | fields @timestamp, `body.requestBody` as req
            | filter ispresent(req) and req like "method=tools/call"
            | parse req /name=(?<tool>[^,}]+)/
            | parse req /arguments=\{(?<args>.*)\}\}\}/
            | display @timestamp, tool, args
            | sort @timestamp desc
            | limit 100
          EOT
        }
      },
    ]
  })
}
