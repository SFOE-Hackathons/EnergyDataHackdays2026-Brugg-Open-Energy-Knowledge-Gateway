# ---------------------------------------------------------------------------
# AgentCore: the MCP gateway and the runtime that hosts the server.
# Opt-in - this is the part that bills.
# ---------------------------------------------------------------------------

# --- Inbound auth (Cognito, machine-to-machine) ------------------------------

resource "aws_cognito_user_pool" "this" {
  count = var.enable_agentcore ? 1 : 0

  name = "${local.name_prefix}-pool"
}

resource "aws_cognito_user_pool_domain" "this" {
  count = var.enable_agentcore ? 1 : 0

  # Must be globally unique across all of Cognito, hence the account suffix.
  domain       = "${local.name_prefix}-${local.account_id}"
  user_pool_id = aws_cognito_user_pool.this[0].id
}

resource "aws_cognito_resource_server" "this" {
  count = var.enable_agentcore ? 1 : 0

  identifier   = "bfe-knowledge-gateway"
  name         = "${local.name_prefix}-gateway"
  user_pool_id = aws_cognito_user_pool.this[0].id

  scope {
    scope_name        = "invoke"
    scope_description = "Invoke the knowledge gateway MCP tools"
  }
}

resource "aws_cognito_user_pool_client" "this" {
  count = var.enable_agentcore ? 1 : 0

  name         = "${local.name_prefix}-client"
  user_pool_id = aws_cognito_user_pool.this[0].id

  # Machine-to-machine: an MCP client fetches a token with its own credentials.
  generate_secret                      = true
  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["${aws_cognito_resource_server.this[0].identifier}/invoke"]
  explicit_auth_flows                  = ["ALLOW_REFRESH_TOKEN_AUTH"]

  depends_on = [aws_cognito_resource_server.this]
}

# --- Gateway -----------------------------------------------------------------

resource "aws_bedrockagentcore_gateway" "this" {
  count = var.enable_agentcore ? 1 : 0

  name        = "${local.name_prefix}-gateway"
  description = "MCP gateway over the BFE energy knowledge base"
  role_arn    = aws_iam_role.gateway.arn

  protocol_type   = "MCP"
  authorizer_type = "CUSTOM_JWT"

  # Surfaces the underlying API error instead of a generic one. Useful while
  # the gateway is being built; consider dropping it once tools are stable.
  exception_level = var.gateway_exception_level

  authorizer_configuration {
    custom_jwt_authorizer {
      discovery_url = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.this[0].id}/.well-known/openid-configuration"
      # Cognito client_credentials tokens carry client_id, not aud, so the
      # client allowlist is the correct control here.
      allowed_clients = [aws_cognito_user_pool_client.this[0].id]
    }
  }

  # These reached the live gateway by hand before Terraform managed it. They
  # are real behaviour, not cosmetics: the instructions are what an MCP client
  # reads to decide how to query the corpus, and dropping them silently
  # degrades every answer. Codified here so an apply preserves them.
  protocol_configuration {
    mcp {
      instructions       = var.gateway_mcp_instructions
      supported_versions = var.gateway_mcp_supported_versions

      streaming_configuration {
        enable_response_streaming = var.gateway_enable_response_streaming
      }
    }
  }
}

# --- Targets -----------------------------------------------------------------
#
# A gateway with no target is an authenticated endpoint that exposes no tools.
# Both targets below were created by hand first and are codified here so the
# gateway stops being hand-managed; the shapes mirror the live resources
# exactly, because a schema that disagrees with the backing tool sends clients
# parameters it rejects.

# The Knowledge Base needs no Lambda: AgentCore ships a first-party connector
# for it. connectorId bedrock-knowledge-bases exposes the KB's Retrieve
# operation directly, signed with the gateway's own IAM role - which already
# holds bedrock:Retrieve on this KB via the kb-read policy.
#
# Note the connector's own docs are easy to misread: the `source.connector_id`
# examples in the provider docs are INFERENCE connectors (bedrock-mantle,
# openai, anthropic). The mcp connector list is different and includes this one.
resource "aws_bedrockagentcore_gateway_target" "kb_connector" {
  count = var.enable_agentcore && var.knowledge_base_connector_enabled ? 1 : 0

  name               = var.knowledge_base_connector_target_name
  gateway_identifier = aws_bedrockagentcore_gateway.this[0].gateway_id

  credential_provider_configuration {
    gateway_iam_role {}
  }

  target_configuration {
    mcp {
      connector {
        source {
          connector_id = "bedrock-knowledge-bases"
          version      = "1.0.0"
        }

        configuration {
          name = "Retrieve"

          # Fixed server-side, so a client cannot ask for more than this.
          parameter_values = jsonencode({
            knowledgeBaseId = var.knowledge_base_id
            retrievalConfiguration = {
              # A MANAGED knowledge base rejects vectorSearchConfiguration -
              # managedSearchConfiguration is the only accepted shape here.
              managedSearchConfiguration = {
                numberOfResults = var.knowledge_base_connector_results
              }
            }
          })
        }
      }
    }
  }
}

# The graph tool is a Lambda this stack does not own, so its ARN is a variable.
resource "aws_bedrockagentcore_gateway_target" "graph_tool" {
  count = var.enable_agentcore && var.graph_tool_lambda_arn != null ? 1 : 0

  name               = var.graph_tool_target_name
  gateway_identifier = aws_bedrockagentcore_gateway.this[0].gateway_id
  # Target-level description, distinct from the tool-level one below. It was
  # set by hand; omitting it here would silently null it out.
  description = var.graph_tool_target_description

  credential_provider_configuration {
    gateway_iam_role {}
  }

  target_configuration {
    mcp {
      lambda {
        lambda_arn = var.graph_tool_lambda_arn

        tool_schema {
          inline_payload {
            name        = var.graph_tool_name
            description = var.graph_tool_description

            input_schema {
              type = "object"

              property {
                name        = "entity"
                type        = "string"
                description = "The label as the caller types it. Matched case-insensitively, whitespace trimmed."
                required    = true
              }

              property {
                name        = "direction"
                type        = "string"
                description = " out | in | both. Which way a connection may point to count."
              }
            }
          }
        }
      }
    }
  }
}

# The feedback tool is the voluntary half of knowing what people ask: the
# client's model calls it when a user corrects, confirms or complains, so it
# records the question together with the answer and the reaction. It fires only
# when the model chooses to, which is why it does not replace capturing traffic
# at the gateway - it explains the calls rather than counting them.
#
# Like the graph tool, the Lambda behind it is not ours; only the target is.
# The schema below mirrors the live target field for field, because a schema
# that disagrees with the backing function sends it arguments it rejects.
resource "aws_bedrockagentcore_gateway_target" "feedback_tool" {
  count = var.enable_agentcore && var.feedback_tool_lambda_arn != null ? 1 : 0

  name               = var.feedback_tool_target_name
  gateway_identifier = aws_bedrockagentcore_gateway.this[0].gateway_id
  description        = var.feedback_tool_target_description

  credential_provider_configuration {
    gateway_iam_role {}
  }

  target_configuration {
    mcp {
      lambda {
        lambda_arn = var.feedback_tool_lambda_arn

        tool_schema {
          inline_payload {
            name        = var.feedback_tool_name
            description = var.feedback_tool_description

            input_schema {
              type = "object"

              # Only the question is required. Demanding the answer and the
              # reaction as well would mean a client that has just been
              # corrected records nothing at all, which is the moment the
              # feedback is worth most.
              property {
                name        = "user_question"
                type        = "string"
                description = "The user's question, verbatim."
                required    = true
              }

              property {
                name        = "answer"
                type        = "string"
                description = "The answer that was given to that question, verbatim."
              }

              property {
                name        = "user_reaction"
                type        = "string"
                description = "What the user said about the answer — the correction, complaint, or approval, in their own words."
              }

              property {
                name        = "source"
                type        = "string"
                description = "Where the question came from — the channel, client, or session the user asked it through."
              }
            }
          }
        }
      }
    }
  }
}

# The SPARQL tool is the second half of the graph story: the neighbours tool
# walks one hop from a label, this one answers an arbitrary read-only query.
# It was created by hand on 2026-09-11, after the gateway was already under
# Terraform, and went unnoticed because the gateway role carries a hand-made
# vl-lambda-invoke policy granting lambda:InvokeFunction on function:*, which
# makes any new target work without Terraform hearing about it.
#
# Its schema is reproduced from the live target verbatim, including the
# tool-level name vl-graph-sparql-tool, which deliberately differs from the
# target name vl-graphsearch-tool - the client calls the former.
resource "aws_bedrockagentcore_gateway_target" "graphsearch_tool" {
  count = var.enable_agentcore && var.graphsearch_tool_lambda_arn != null ? 1 : 0

  name               = var.graphsearch_tool_target_name
  gateway_identifier = aws_bedrockagentcore_gateway.this[0].gateway_id
  description        = var.graphsearch_tool_target_description

  credential_provider_configuration {
    gateway_iam_role {}
  }

  target_configuration {
    mcp {
      lambda {
        lambda_arn = var.graphsearch_tool_lambda_arn

        tool_schema {
          inline_payload {
            name        = var.graphsearch_tool_name
            description = var.graphsearch_tool_description

            input_schema {
              type = "object"

              # Nothing is required, matching the live schema. The query is
              # optional on purpose: calling with no arguments returns the
              # graph schema, which is the documented first call.
              property {
                name        = "query"
                type        = "string"
                description = "SELECT, ASK, CONSTRUCT or DESCRIBE. Omit it to get the schema instead of rows. Prefixes: kg: rel: ent: doc: lbl: asr:. With a variable predicate add FILTER STRSTARTS(STR(?p), STR(rel:))."
              }

              property {
                name        = "limit"
                type        = "integer"
                description = "Maximum rows or triples returned. Default 50, hard maximum 500; a larger value is clamped, not rejected."
              }
            }
          }
        }
      }
    }
  }
}

# --- Runtime -----------------------------------------------------------------

resource "aws_bedrockagentcore_agent_runtime" "this" {
  count = var.enable_agentcore && var.agent_runtime_container_uri != null ? 1 : 0

  # AgentCore runtime names do not accept hyphens.
  agent_runtime_name = replace("${local.name_prefix}_runtime", "-", "_")
  description        = "MCP server for the BFE energy knowledge gateway"
  role_arn           = aws_iam_role.agent_runtime.arn

  agent_runtime_artifact {
    container_configuration {
      container_uri = var.agent_runtime_container_uri
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  environment_variables = {
    BEDROCK_KB_ID             = var.knowledge_base_id
    BEDROCK_KB_DATA_SOURCE_ID = var.knowledge_base_data_source_id
    AWS_REGION                = var.aws_region
  }
}

resource "aws_bedrockagentcore_agent_runtime_endpoint" "this" {
  count = var.enable_agentcore && var.agent_runtime_container_uri != null ? 1 : 0

  name             = "default"
  agent_runtime_id = aws_bedrockagentcore_agent_runtime.this[0].agent_runtime_id
  description      = "Default endpoint for the MCP server runtime"
}
