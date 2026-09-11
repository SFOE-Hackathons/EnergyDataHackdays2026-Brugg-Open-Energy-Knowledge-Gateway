locals {
  # Environment comes from the workspace, so `make plan ENV=bfe` cannot
  # accidentally apply sandbox values against BFE's account.
  environment = terraform.workspace == "default" ? "sandbox" : terraform.workspace

  name_prefix = "${var.project}-${local.environment}"

  common_tags = {
    Project     = "bfe-knowledge-gateway"
    Environment = local.environment
    ManagedBy   = "terraform"
    Event       = "energy-data-hackdays-2026"
    # BFE's account is shared with their own resources and eleven other users.
    # The tag is what makes ours filterable in the console and in Cost Explorer.
    Owner = "VirtusLab"
  }

  account_id = data.aws_caller_identity.current.account_id

  bucket_names = {
    for key, cfg in var.buckets :
    key => coalesce(cfg.name, "${local.name_prefix}-${key}")
  }

  kb_arn = data.external.knowledge_base.result.arn

  # An S3-backed data source exposes its bucket directly; a MANAGED connector
  # buries it in a JSON string, which the jq in data.tf unpacks. Either way the
  # variable stays as a fallback for a shape neither branch matches.
  # distinct() collapses the overlap when both resolve to the same bucket.
  kb_source_bucket_arns = distinct(compact(concat(
    [data.external.knowledge_base_data_source.result.bucket_arn],
    var.extra_kb_source_bucket_arns,
  )))

  kb_source_object_arns = [for arn in local.kb_source_bucket_arns : "${arn}/*"]

  # Buckets a SERVICE principal must read or write need a customer-managed key;
  # everything else can use whatever bucket_kms_key_id says (null = the
  # AWS-managed aws/s3 key).
  #
  # Log delivery wins the tie when a bucket is marked both. A Lambda denied
  # s3:GetObject fails loudly and is easy to diagnose; log delivery under the
  # wrong key writes objects that exist and are unreadable, and says nothing.
  bucket_kms_key_ids = {
    for key, cfg in var.buckets :
    key => (
      try(cfg.log_delivery_target, false) && local.create_log_delivery_key
      ? aws_kms_key.log_delivery[0].arn
      : try(cfg.lambda_readable, false) && local.create_artifact_key
      ? aws_kms_key.artifacts[0].arn
      : var.bucket_kms_key_id
    )
  }

  # Whether an invoke policy is needed at all. Derived from VARIABLES only:
  # a count that reads a resource attribute is unknown until apply, which
  # breaks `terraform import` with "Invalid count argument".
  gateway_needs_invoke_policy = (
    var.enable_lambda
    || var.graph_tool_lambda_arn != null
    || var.feedback_tool_lambda_arn != null
    || var.graphsearch_tool_lambda_arn != null
    || length(var.gateway_extra_invokable_lambda_arns) > 0
  )

  # Every Lambda the gateway must be able to invoke: our own function if we
  # build one, the graph and feedback tools, plus anything named explicitly.
  # A target whose function is missing from here reports READY but cannot run:
  # the status validates configuration, not permission.
  gateway_invokable_lambda_arns = distinct(compact(concat(
    [try(aws_lambda_function.mcp_tools[0].arn, "")],
    [coalesce(var.graph_tool_lambda_arn, "")],
    [coalesce(var.feedback_tool_lambda_arn, "")],
    [coalesce(var.graphsearch_tool_lambda_arn, "")],
    var.gateway_extra_invokable_lambda_arns,
  )))

  owned_bucket_arns        = [for b in aws_s3_bucket.this : b.arn]
  owned_bucket_object_arns = [for b in aws_s3_bucket.this : "${b.arn}/*"]
}
