# ---------------------------------------------------------------------------
# Account / identity
# ---------------------------------------------------------------------------

variable "aws_profile" {
  description = "Named AWS CLI profile for the TARGET account (sandbox or BFE)."
  type        = string
}

variable "aws_region" {
  description = "Region for every resource in this stack."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Short slug used to prefix generated resource names."
  type        = string
  default     = "bfe-kg"
}

# ---------------------------------------------------------------------------
# Knowledge Base - READ ONLY. BFE owns it; we never manage it.
# ---------------------------------------------------------------------------

variable "knowledge_base_id" {
  description = "Existing Bedrock Knowledge Base id to read (e.g. MQJ3YJI3UB)."
  type        = string

  validation {
    condition     = can(regex("^[A-Z0-9]{10}$", var.knowledge_base_id))
    error_message = "A Bedrock knowledge base id is 10 uppercase alphanumerics."
  }
}

variable "knowledge_base_data_source_id" {
  description = "Data source id belonging to that knowledge base (e.g. WBRJO3EDMR)."
  type        = string

  validation {
    condition     = can(regex("^[A-Z0-9]{10}$", var.knowledge_base_data_source_id))
    error_message = "A Bedrock data source id is 10 uppercase alphanumerics."
  }
}

variable "extra_kb_source_bucket_arns" {
  description = <<-EOT
    Bucket ARNs backing the knowledge base that cannot be derived from the data
    source. MANAGED connectors hide their bucket behind opaque connector
    parameters, so for those the bucket must be named here.
  EOT
  type        = list(string)
  default     = []
}

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

variable "buckets" {
  description = <<-EOT
    Buckets this stack owns, keyed by logical role. Omit `name` to get a
    generated `<project>-<env>-<key>` name; set it to adopt an existing bucket.
  EOT
  type = map(object({
    name = optional(string)
    # Lambda reads deployment packages as a SERVICE principal, not as you.
    # Set this on any bucket holding a Lambda zip: it adds a bucket policy for
    # lambda.amazonaws.com and, under KMS, switches the bucket to a
    # customer-managed key the service principal can actually be granted.
    lambda_readable = optional(bool, false)
    # CloudWatch Logs writes vended log archives as the
    # delivery.logs.amazonaws.com SERVICE principal, not as you. Set this on a
    # bucket receiving delivered logs: it adds the two AWSLogDelivery bucket
    # policy statements, switches the bucket to a customer-managed KMS key and
    # turns the S3 Bucket Key off. Under the AWS-managed aws/s3 key AWS
    # delivers the objects "in an unreadable format" rather than failing.
    log_delivery_target = optional(bool, false)
  }))
  default = {}
}

# ---------------------------------------------------------------------------
# Compute (opt-in - both cost money, and the budget is nearly spent)
# ---------------------------------------------------------------------------

variable "enable_lambda" {
  description = "Create the MCP tool Lambda and its dependency layer."
  type        = bool
  default     = false
}

variable "enable_gateway_observability" {
  description = <<-EOT
    Deliver the gateway's APPLICATION_LOGS to CloudWatch Logs, and to S3 if a
    bucket is marked log_delivery_target. This is what captures the question
    text: it appears in body.requestBody of the application log record and
    nowhere else - the TRACES spans carry tool.name and latency but never the
    tool arguments.

    Has no effect unless enable_agentcore is also true, so `make down` takes it
    away along with the gateway it observes. Billing: log ingestion and
    storage, plus one customer-managed KMS key if the S3 archive is used.
  EOT
  type        = bool
  default     = false
}

variable "lambda_artifact_bucket" {
  description = "Bucket holding the Lambda zip and layer zip."
  type        = string
  default     = null
}

variable "lambda_package_key" {
  description = "S3 key of the Lambda deployment zip."
  type        = string
  default     = null
}

variable "lambda_layer_key" {
  description = "S3 key of the dependency layer zip."
  type        = string
  default     = null
}

variable "enable_agentcore" {
  description = "Create the AgentCore runtime, gateway and Cognito pool."
  type        = bool
  default     = false
}

variable "agent_runtime_container_uri" {
  description = "ECR image URI for the MCP server. Built and pushed outside Terraform."
  type        = string
  default     = null
}

# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------

variable "budget_limit_usd" {
  description = "Monthly cost budget ceiling."
  type        = string
  default     = "200"
}

variable "budget_notification_emails" {
  description = "Addresses that receive budget alarms. Empty disables the budget."
  type        = list(string)
  default     = []
}

variable "log_retention_days" {
  description = "Retention for log groups this stack owns. Six groups in the sandbox never expire today."
  type        = number
  default     = 14
}

# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

variable "adopt_existing_buckets" {
  description = <<-EOT
    Buckets that already exist and should be brought under Terraform rather than
    created. Keys must match keys in `buckets`; values are the live bucket names.
    Empty (the default) makes the import block in imports.tf inert.
  EOT
  type        = map(string)
  default     = {}
}

# ---------------------------------------------------------------------------
# Bucket encryption
# ---------------------------------------------------------------------------

variable "bucket_sse_algorithm" {
  description = <<-EOT
    Default server-side encryption for the buckets this stack owns.

    Must be "aws:kms" in the BFE account: an org resource control policy
    (p-02xqecd89i) denies PutObject on anything not encrypted with KMS, and an
    RCP outranks IAM, the bucket policy and AdministratorAccess alike. With
    AES256 the bucket is created fine and every upload then fails with
    AccessDenied.
  EOT
  type        = string
  default     = "AES256"

  validation {
    condition     = contains(["AES256", "aws:kms"], var.bucket_sse_algorithm)
    error_message = "bucket_sse_algorithm must be AES256 or aws:kms."
  }
}

variable "bucket_kms_key_id" {
  description = <<-EOT
    KMS key for bucket encryption when bucket_sse_algorithm is "aws:kms".
    Leave null to use the AWS-managed aws/s3 key, which is free and needs no
    key policy - the same key BFE uses on their own corpus bucket.
  EOT
  type        = string
  default     = null
}

# ---------------------------------------------------------------------------
# AgentCore gateway behaviour
# ---------------------------------------------------------------------------

variable "gateway_mcp_instructions" {
  description = <<-EOT
    Text an MCP client reads to learn how to query this corpus. Not cosmetic -
    it is what tells a model that documents are named by date and that the
    corpus is multilingual, so leaving it empty measurably degrades answers.
  EOT
  type        = string
  default     = null
}

variable "gateway_mcp_supported_versions" {
  description = "MCP protocol versions the gateway accepts. null lets AWS pick its default set."
  type        = list(string)
  default     = null
}

variable "gateway_enable_response_streaming" {
  description = "Stream MCP responses back to the client."
  type        = bool
  default     = false
}

variable "gateway_exception_level" {
  description = "Set to DEBUG to surface the underlying API error rather than a generic one."
  type        = string
  default     = null

  validation {
    condition     = var.gateway_exception_level == null || var.gateway_exception_level == "DEBUG"
    error_message = "gateway_exception_level must be DEBUG or null."
  }
}

variable "knowledge_base_connector_enabled" {
  description = <<-EOT
    Expose the knowledge base through the first-party bedrock-knowledge-bases
    connector. This needs no Lambda: the gateway calls Retrieve itself, signed
    with its own IAM role.
  EOT
  type        = bool
  default     = false
}

variable "knowledge_base_connector_target_name" {
  description = "Target name for the knowledge base connector."
  type        = string
  default     = "kb-search"
}

variable "knowledge_base_connector_results" {
  description = <<-EOT
    numberOfResults fixed into the connector. It is set server-side, so a
    client cannot request more than this.
  EOT
  type        = number
  default     = 5
}

variable "graph_tool_lambda_arn" {
  description = "ARN of an existing Lambda to expose as a graph tool. Null skips the target."
  type        = string
  default     = null
}

variable "graph_tool_target_name" {
  description = "Target name for the graph tool."
  type        = string
  default     = "graph-neighbors-tool"
}

variable "graph_tool_name" {
  description = "Tool name the graph Lambda answers to. Must match what the function expects."
  type        = string
  default     = "DefaultTool"
}

variable "graph_tool_description" {
  description = "Agent-facing description of the graph tool."
  type        = string
  default     = "Default tool description"
}

variable "gateway_extra_invokable_lambda_arns" {
  description = <<-EOT
    Additional Lambda ARNs the gateway role may invoke, beyond the targets this
    stack defines. Prefer listing ARNs over a wildcard: the hand-made policy
    this replaces granted lambda:InvokeFunction on function:*.
  EOT
  type        = list(string)
  default     = []
}

variable "graph_tool_target_description" {
  description = "Target-level description of the graph tool, distinct from the tool-level one."
  type        = string
  default     = null
}

# --- Feedback tool -----------------------------------------------------------
# A second Lambda-backed target this stack does not own, modelled exactly like
# the graph tool. Every field defaults to null rather than to a placeholder: a
# placeholder default is what let an apply nearly overwrite the graph tool's
# real schema with "DefaultTool". Null fails loudly at plan instead.

variable "feedback_tool_lambda_arn" {
  description = "ARN of an existing Lambda that records question/answer feedback. Null skips the target."
  type        = string
  default     = null
}

variable "feedback_tool_target_name" {
  description = "Target name for the feedback tool."
  type        = string
  default     = null
}

variable "feedback_tool_target_description" {
  description = "Target-level description of the feedback tool, distinct from the tool-level one."
  type        = string
  default     = null
}

variable "feedback_tool_name" {
  description = "Tool name the feedback Lambda answers to. Must match what the function expects."
  type        = string
  default     = null
}

variable "feedback_tool_description" {
  description = <<-EOT
    Agent-facing description of the feedback tool. This is the text a client
    reads to decide whether to call it at all, so it is phrased as an
    instruction rather than a label. Changing it changes behaviour.
  EOT
  type        = string
  default     = null
}

variable "adopt_feedback_tool_target" {
  description = <<-EOT
    Import id of an existing feedback-tool target to adopt, as
    "<gateway-id>,<target-id>". Null creates the target instead of adopting it.
    Clear this once the import has been applied and state has settled.
  EOT
  type        = string
  default     = null
}

variable "graphsearch_tool_lambda_arn" {
  description = "ARN of an existing Lambda that answers read-only SPARQL over the knowledge graph. Null skips the target."
  type        = string
  default     = null
}

variable "graphsearch_tool_target_name" {
  description = "Target name for the SPARQL tool."
  type        = string
  default     = null
}

variable "graphsearch_tool_target_description" {
  description = "Target-level description of the SPARQL tool, distinct from the tool-level one."
  type        = string
  default     = null
}

variable "graphsearch_tool_name" {
  description = "Tool name the SPARQL Lambda answers to. Must match what the function expects."
  type        = string
  default     = null
}

variable "graphsearch_tool_description" {
  description = <<-EOT
    Agent-facing description of the SPARQL tool. It tells the client to fetch
    the schema before guessing a predicate, which is the difference between
    rows and a silent empty result. Changing it changes behaviour.
  EOT
  type        = string
  default     = null
}

variable "adopt_graphsearch_tool_target" {
  description = <<-EOT
    Import id of an existing SPARQL-tool target to adopt, as
    "<gateway-id>,<target-id>". Null creates the target instead of adopting it.
    Clear this once the import has been applied and state has settled.
  EOT
  type        = string
  default     = null
}
