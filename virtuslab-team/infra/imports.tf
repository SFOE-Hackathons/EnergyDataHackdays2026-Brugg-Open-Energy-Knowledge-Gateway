# ---------------------------------------------------------------------------
# Adopting resources that already exist.
#
# Everything lives in the ROOT module on purpose: config-driven import cannot
# target a resource inside a module (hashicorp/terraform#35596), and this repo
# needs import to work.
#
# Workflow:
#   1. name the live resource in envs/<env>.tfvars
#   2. make plan ENV=<env>          -> shows "will be imported", not "created"
#   3. make apply ENV=<env>
#   4. delete the entry once state is settled
#
# To generate HCL for something not modelled here:
#   make import-scan ENV=<env>
# which runs `terraform plan -generate-config-out=generated_resources.tf`.
# Two known potholes, both still open upstream:
#   - generation is flagged experimental and emits non-idiomatic config
#   - it struggles with discriminated-union blocks, so the AgentCore
#     authorizer_configuration / target_configuration output needs hand-editing
# generated_resources.tf is gitignored; treat it as scratch.
# ---------------------------------------------------------------------------

import {
  for_each = var.adopt_existing_buckets

  to = aws_s3_bucket.this[each.key]
  id = each.value
}

# The feedback-tool target was built by hand on 2026-09-10, on the same gateway
# this stack manages. Adopting it is not tidiness: while it sat outside
# Terraform its schema and description were whatever the console last wrote,
# and the gateway role had no permission to invoke the function behind it.
#
# Driven by a variable so it is inert in every environment that does not set
# one - an unconditional import block would fail the plan for test and sandbox,
# where the target does not exist.
import {
  for_each = var.adopt_feedback_tool_target != null ? { adopt = var.adopt_feedback_tool_target } : {}

  to = aws_bedrockagentcore_gateway_target.feedback_tool[0]
  id = each.value
}

# The SPARQL target was built by hand on 2026-09-11, after the gateway was
# already managed here - drift that appeared between writing this branch and
# planning it. Adopting it rather than creating a second target keeps its live
# schema, and naming its Lambda is what puts the function into the gateway
# role's invoke-mcp-tools policy. Same variable-driven shape as above so it
# stays inert wherever the target does not exist.
import {
  for_each = var.adopt_graphsearch_tool_target != null ? { adopt = var.adopt_graphsearch_tool_target } : {}

  to = aws_bedrockagentcore_gateway_target.graphsearch_tool[0]
  id = each.value
}

# --- Templates ---------------------------------------------------------------
# Uncomment, fill in the id, plan, apply, then delete.
#
# import {
#   to = aws_iam_role.gateway
#   id = "AmazonBedrockAgentCoreGatewayEnergy"
# }
#
# import {
#   to = aws_lambda_function.mcp_tools[0]
#   id = "mcp-agent-skill"
# }
#
# import {
#   to = aws_cognito_user_pool.this[0]
#   id = "us-east-1_xrBpCkqoI"
# }
#
# The knowledge base is deliberately absent: it belongs to BFE and is read
# through data.external.knowledge_base. Never import it.
#
# BFE's own gateway in 542202863496 is a candidate for import, not creation -
# they built and published it, and clients may be pointed at its URL.
# enable_agentcore is TRUE for ENV=bfe and we run our own gateway alongside
# theirs, so the two coexist; theirs stays unmanaged and untouched. Revisit
# only if clients are moved onto it.
#
# It is bfe-energy-knowledge-open-v6rj5uttek as of 2026-09-11. This note said
# sandbox-bfe-public-kb-8thmswsvit, which list-gateways no longer returns -
# BFE replaced it. Read the id off the account rather than trusting this line.
