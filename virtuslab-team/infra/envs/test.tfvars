# Disposable environment. Same account as sandbox (790954698087), but it shares
# nothing with it: separate workspace, separate state, separate resource names.
#
# This is the safe way to prove the stack really builds before touching the
# sandbox or BFE's account:
#
#   make plan    ENV=test
#   make apply   ENV=test
#   make destroy ENV=test
#
# Everything is named bfe-kg-test-*, so it cannot collide with bfe-kg-sandbox-*.
# Nothing here is adopted, so no existing resource is ever taken over.

aws_profile = "bedrock-kb"
aws_region  = "us-east-1"

# The KB is READ ONLY - a data source cannot modify it. Pointing test at the
# real knowledge base is safe, and it is the only way to prove the IAM policy
# resolves against a genuine MANAGED-type KB.
knowledge_base_id             = "MQJ3YJI3UB"
knowledge_base_data_source_id = "WBRJO3EDMR"

# Read-only grant on the real corpus bucket. Grants a policy, touches nothing.
extra_kb_source_bucket_arns = [
  "arn:aws:s3:::sfoe-data-energy-monitoring",
]

# Fresh, empty buckets: bfe-kg-test-artifacts and bfe-kg-test-graph.
# Empty means `terraform destroy` can actually remove them afterwards.
buckets = {
  artifacts = {}
  graph     = {}
}

# Deliberately empty. Adopting an existing bucket here would defeat the point.
adopt_existing_buckets = {}

# No budget in the test env: a second budget would re-send SNS subscription
# confirmation emails on every rebuild for no benefit.
budget_notification_emails = []

log_retention_days = 1

# Start with the free tier of the stack. To exercise the billable parts:
#   make plan ENV=test EXTRA='-var enable_agentcore=true'
# which adds the Cognito pool and the AgentCore gateway. The AgentCore runtime
# additionally needs -var agent_runtime_container_uri=<ecr-uri>.
enable_lambda    = false
enable_agentcore = false

# Off by default like the rest of the billable half. To exercise it:
#   make plan ENV=test EXTRA='-var enable_agentcore=true -var enable_gateway_observability=true'
# Note this env is AES256, so the KMS half of log delivery - the riskiest part
# of the change - is NOT exercised here. Set bucket_sse_algorithm = "aws:kms"
# first if that is what you are testing.
enable_gateway_observability = false
