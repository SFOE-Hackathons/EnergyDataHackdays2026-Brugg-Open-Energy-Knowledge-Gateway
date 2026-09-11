# BFE sandbox account 542202863496.
# Workspace: bfe
#
# Values verified against the live account on 2026-09-10 with:
#   aws bedrock-agent list-knowledge-bases --profile bfe --region eu-central-1
#   aws bedrock-agent list-data-sources --knowledge-base-id ZPVWAEHXNB \
#     --profile bfe --region eu-central-1
#
# knowledge_base_id is validated, so a wrong or unset value fails at plan time
# rather than producing a stack wired to a KB that does not exist.

aws_profile = "bfe"

# 542202863496 is shared - user01..user12 all hold AdministratorAccess, and BFE
# built the KB, gateway and Cognito pool here themselves. The "vl-" prefix makes
# it obvious at a glance which resources are ours. Set here rather than in
# variables.tf so sandbox and test keep their existing names and are not
# force-replaced.
project = "vl-bfe-kg"

# eu-central-1, not us-east-1 as everywhere else. BFE built the knowledge base,
# the corpus bucket, the AgentCore gateway and the Cognito pool in Frankfurt;
# every other region in this account is empty and bills zero. The state bucket
# stays in us-east-1 regardless - the Makefile passes that profile separately.
aws_region = "eu-central-1"

knowledge_base_id             = "ZPVWAEHXNB" # KB-bfe-public, MANAGED type
knowledge_base_data_source_id = "B1NDHNS8TN" # sandbox-bfe-public-data-pdf

# A MANAGED connector hides its bucket inside an opaque connectorParameters
# JSON string, so locals.tf cannot derive it - it has to be named here or the
# KB read policy grants nothing.
extra_kb_source_bucket_arns = ["arn:aws:s3:::sandbox-bfe-public-data-pdf"]

# The org resource control policy p-02xqecd89i denies s3:PutObject unless the
# object is KMS-encrypted. An RCP outranks IAM, the bucket policy and
# AdministratorAccess, so this is not fixable with permissions - the bucket
# default has to be KMS. Verified 2026-09-10: PutObject with AES256 is denied,
# with aws:kms it succeeds. null uses the AWS-managed aws/s3 key, which is free
# and is what BFE use on sandbox-bfe-public-data-pdf.
bucket_sse_algorithm = "aws:kms"
bucket_kms_key_id    = null

# Fresh buckets, generated names: bfe-kg-bfe-artifacts, bfe-kg-bfe-graph.
buckets = {
  # lambda_readable: this bucket holds the MCP tool deployment package, and
  # Lambda fetches it as the lambda.amazonaws.com service principal rather
  # than as the caller. That needs a bucket policy naming the service, and a
  # customer-managed KMS key - the AWS-managed aws/s3 key has an immutable
  # policy that covers account IAM principals only, so a service principal can
  # never be granted decrypt on it. Without both halves CreateFunction fails
  # with "Lambda is unable to access the specified S3 object".
  artifacts = { lambda_readable = true }
  graph     = {}

  # The question corpus. log_delivery_target because CloudWatch Logs writes
  # these objects as delivery.logs.amazonaws.com, a service principal - the
  # same shape of problem as lambda_readable above, with a nastier failure
  # mode: under the AWS-managed aws/s3 key AWS documents that the logs arrive
  # "in an unreadable format" rather than being denied. The flag gives this
  # bucket its own customer-managed key and turns its S3 Bucket Key off.
  questions = { log_delivery_target = true }
}

# No budget or alerting in this account: BFE owns cost control here, and their
# SCP denies SNS:CreateTopic anyway, which failed the first apply. An empty
# email list zeroes the count on the budget, the SNS topic and the subscription.
# budget_limit_usd is unused while this is empty; kept for when it is not.
budget_limit_usd           = "200"
budget_notification_emails = []
log_retention_days         = 14

# We run our own gateway rather than importing BFE's sandbox-bfe-public-kb:
# we want an endpoint and a Cognito pool we control. Theirs stays untouched.
#
# The gateway comes up with no targets until enable_lambda is also true, which
# needs a deployment package in lambda_artifact_bucket / lambda_package_key -
# built and uploaded outside Terraform. Until then this is an authenticated
# MCP endpoint that exposes no tools.
enable_lambda    = false
enable_agentcore = true

# The point of the exercise: find out what BFE's people actually ask the
# corpus. The question text exists in exactly one place - body.requestBody of
# the gateway's APPLICATION_LOGS record - and it is a Java-map-style string,
# not JSON, so it is pulled out with a regex at read time. The saved queries
# are in the CloudWatch console under Queries, filed as vl-bfe-kg-bfe/*.
#
# Traces are deliberately absent: SCP p-bvj3x4lc denies xray:* account-wide,
# so Transaction Search cannot be enabled here, and spans carry tool.name and
# latency but never the arguments. Nothing is lost. See RUNBOOK.md.
enable_gateway_observability = true

# Read off the live gateway on 2026-09-10, re-read on 2026-09-11 after it was
# edited in the console mid-change. These were configured by hand before
# Terraform managed the gateway, and an empty protocol_configuration would
# silently wipe them.
#
# The second line is not decoration. It tells the client to expand entity
# labels through the graph tool before retrieving, which is the workflow this
# corpus is tuned for, and its closing sentence is what makes a client call the
# feedback tool at all. Both were missing here, so an apply would have deleted
# them and quietly degraded every answer.
#
# "retreival" is a typo in the live value, reproduced verbatim on purpose: this
# file records what the gateway says. Correcting prose here would be an
# undeclared behaviour change - fix it on the gateway first, then here.
gateway_mcp_instructions = "Search tool over the Swiss Federal Office of Energy (SFOE/BFE) public energy publications. Passages are returned with the source document name, which begins with the publication date as YYYY-MM-DD. The corpus is multilingual (German, French, Italian, English) and a query in one language will return passages in the others.\nBest way to use: extract 1–2 word entity labels, expand each via vl-graph-neighbors-tool first, then retrieve for every entity and answer only from results. With consent from user send feedback using tool to help improve retreival process"

gateway_mcp_supported_versions    = ["2025-03-26", "2025-11-25", "2026-07-28"]
gateway_enable_response_streaming = false
gateway_exception_level           = "DEBUG"

# --- Gateway targets ---------------------------------------------------------
# Both were created by hand on 2026-09-10 and are codified here so the gateway
# stops being hand-managed. Names match the live targets exactly so Terraform
# adopts them rather than creating duplicates.

# The KB needs no Lambda: AgentCore ships a first-party connector that calls
# Retrieve directly, signed with the gateway's IAM role.
knowledge_base_connector_enabled     = true
knowledge_base_connector_target_name = "vl-KB-bfe-public"
knowledge_base_connector_results     = 5

# Graph tool: a Lambda this stack does not own.
graph_tool_lambda_arn  = "arn:aws:lambda:eu-central-1:542202863496:function:vl_gb_tool"
graph_tool_target_name = "vl-graph-neighbors-tool"
# Tool-level name and description, read off the live target. These were
# placeholders ("DefaultTool" / "Default tool description") and an apply
# would have written them over the real schema - which is what an MCP client
# reads to decide whether and how to call the tool.
graph_tool_name        = "vl-graph-neighbors-tool"
graph_tool_description = "Finds the entity the caller names and returns the entities directly connected to it, with the predicate and the direction of each connection."

# Target-level description, read off the live target so an apply preserves it.
graph_tool_target_description = "Finds the entity the caller names and returns the entities directly connected to it, with the predicate and the direction of each connection."

# Feedback tool: a second Lambda this stack does not own, built by hand on
# 2026-09-10 together with its target. Every value below was read off the live
# target on 2026-09-11 rather than invented, so adopting it is a no-op.
#
# Naming this ARN also fixes a real fault: it is the only thing that puts the
# function into the gateway role's invoke-mcp-tools policy. The function has no
# resource policy of its own, so until now the target reported READY and every
# call to it would have failed with AccessDenied.
feedback_tool_lambda_arn         = "arn:aws:lambda:eu-central-1:542202863496:function:vl-feedback-tool"
feedback_tool_target_name        = "vl-feedback-tool"
feedback_tool_name               = "vl-feedback"
feedback_tool_description        = "Records a question, the answer that was given, and how the user reacted to it, so future answers can be improved. Call it whenever the user corrects, confirms, or complains about an answer."
feedback_tool_target_description = "Records a question, the answer that was given, and how the user reacted to it, so future answers can be improved. Call it whenever the user corrects, confirms, or complains about an answer."

# Adopt the live target rather than creating a second one. Clear this once the
# import has been applied and `make plan` is clean.
adopt_feedback_tool_target = "vl-bfe-kg-bfe-gateway-fhriymckkx,L2JGDKDPKI"

# SPARQL tool: a third Lambda this stack does not own. The target was created
# by hand on 2026-09-11 00:01 UTC - after this branch was written - and was
# found by listing the live gateway's targets on 2026-09-11. Every value below
# is read off target N60B2QPYNS, so adopting it is a no-op.
#
# The tool-level name is NOT the target name: clients call vl-graph-sparql-tool
# while the target is vl-graphsearch-tool. Reproduced as-is; renaming either
# would change what an MCP client sees.
graphsearch_tool_lambda_arn  = "arn:aws:lambda:eu-central-1:542202863496:function:vl-graphsearch-tool"
graphsearch_tool_target_name = "vl-graphsearch-tool"
graphsearch_tool_name        = "vl-graph-sparql-tool"

graphsearch_tool_description        = "Read-only SPARQL over the energy knowledge graph. Call it with no query first to get the schema: predicates are not normalised, so a guessed spelling returns zero rows, not an error."
graphsearch_tool_target_description = "Read-only SPARQL over the energy knowledge graph. Call it with no query first to get the schema: predicates are not normalised, so a guessed spelling returns zero rows, not an error."

# Adopt, do not create. Clear once applied and `make plan` is clean.
adopt_graphsearch_tool_target = "vl-bfe-kg-bfe-gateway-fhriymckkx,N60B2QPYNS"
