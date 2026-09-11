# --- Knowledge base (read-only, owned by BFE) --------------------------------

output "knowledge_base_arn" {
  description = "ARN of the knowledge base this stack reads."
  value       = local.kb_arn
}

output "knowledge_base_name" {
  description = "Human-readable name of the knowledge base."
  value       = data.external.knowledge_base.result.name
}

output "knowledge_base_status" {
  description = "Knowledge base status - anything but ACTIVE means retrieval will fail."
  value       = data.external.knowledge_base.result.status
}

output "knowledge_base_type" {
  description = "MANAGED means AWS owns the vector store; there is none of ours to manage."
  value       = data.external.knowledge_base.result.type
}

output "knowledge_base_source_bucket_arns" {
  description = "Source buckets the KB ingests from, as resolved for the IAM grant."
  value       = local.kb_source_bucket_arns
}

# --- What this stack owns ----------------------------------------------------

output "bucket_names" {
  description = "Buckets created by this stack, keyed by logical role."
  value       = { for k, b in aws_s3_bucket.this : k => b.id }
}

output "role_arns" {
  description = "IAM roles created by this stack."
  value = {
    gateway       = aws_iam_role.gateway.arn
    agent_runtime = aws_iam_role.agent_runtime.arn
    lambda        = aws_iam_role.lambda.arn
  }
}

output "gateway_url" {
  description = "MCP endpoint to point an AI client at."
  value       = try(aws_bedrockagentcore_gateway.this[0].gateway_url, null)
}

output "cognito_token_endpoint" {
  description = "OAuth2 token endpoint for machine-to-machine clients."
  value = try(
    "https://${aws_cognito_user_pool_domain.this[0].domain}.auth.${var.aws_region}.amazoncognito.com/oauth2/token",
    null,
  )
}

output "cognito_client_id" {
  description = "App client id allowed to call the gateway."
  value       = try(aws_cognito_user_pool_client.this[0].id, null)
}

output "cognito_client_secret" {
  description = "App client secret. Read with: terraform output -raw cognito_client_secret"
  value       = try(aws_cognito_user_pool_client.this[0].client_secret, null)
  sensitive   = true
}

output "gateway_log_group" {
  description = "CloudWatch log group receiving the gateway's MCP request and response bodies."
  value       = try(aws_cloudwatch_log_group.gateway[0].name, null)
}

output "gateway_questions_bucket" {
  description = "Bucket holding the durable archive of delivered gateway logs."
  value = try(
    [for key, cfg in local.log_delivery_buckets : aws_s3_bucket.this[key].bucket][0],
    null,
  )
}

output "gateway_dashboard_url" {
  description = "CloudWatch dashboard for gateway traffic and the questions being asked."
  value = try(
    "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards/dashboard/${aws_cloudwatch_dashboard.gateway[0].dashboard_name}",
    null,
  )
}
