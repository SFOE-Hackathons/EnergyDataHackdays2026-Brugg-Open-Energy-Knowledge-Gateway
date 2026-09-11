# VirtusLab sandbox account 790954698087 - the prototype environment.
# Workspace: sandbox

aws_profile = "bedrock-kb"
aws_region  = "us-east-1"

# knowledge-base-monitoring-energy-v2 (type MANAGED) and its data source.
# The other live KB is 3IDM90YHWO / HL2DYI3GHK (knowledge-base-kg-read).
knowledge_base_id             = "MQJ3YJI3UB"
knowledge_base_data_source_id = "WBRJO3EDMR"

# A MANAGED connector hides its bucket behind opaque connector parameters, so
# the KB source bucket cannot be derived from the data source and is named here.
extra_kb_source_bucket_arns = [
  "arn:aws:s3:::sfoe-data-energy-monitoring",
]

buckets = {
  artifacts = { name = "agent-skills-energy-days" }
  graph     = { name = "energy-knowledge-graph" }
}

# Both buckets predate Terraform. Adopt rather than create.
adopt_existing_buckets = {
  artifacts = "agent-skills-energy-days"
  graph     = "energy-knowledge-graph"
}

budget_limit_usd           = "200"
budget_notification_emails = ["gkrol@virtuslab.com"]
log_retention_days         = 14

# Compute is off by default: ~$68 of the $200 budget remains.
enable_lambda    = false
enable_agentcore = false

# Off. The gateway is off here too (enable_agentcore = false), so there would
# be nothing to observe and the delivery source would have no gateway to point
# at. Chained on enable_agentcore in observability.tf, so this is belt and
# braces rather than load-bearing.
enable_gateway_observability = false
