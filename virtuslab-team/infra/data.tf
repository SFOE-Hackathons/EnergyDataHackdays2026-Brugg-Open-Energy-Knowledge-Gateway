data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

# ---------------------------------------------------------------------------
# The Knowledge Base is BFE's. It is READ here and never managed.
#
# This used to go through hashicorp/awscc, which reads via Cloud Control
# GetResource. BFE's organisation denies that account-wide:
#
#   AccessDeniedException: not authorized to perform cloudformation:GetResource
#   ... with an explicit deny in a service control policy
#   arn:aws:organizations::429128461717:policy/o-3r6tcb49g2/.../p-bvj3x4lc
#
# Cloud Control authorises under the cloudformation: namespace even though no
# stack is involved, and an SCP outranks IAM - AdministratorAccess in the
# account cannot lift it. So the read goes through the CLI instead, on
# bedrock:GetKnowledgeBase, which the SCP does not touch.
#
# Keeping it a data source (rather than hardcoding the ARN) preserves the
# property the awscc lookup was there for: a wrong knowledge_base_id fails at
# plan time instead of producing a stack wired to a KB that does not exist.
#
# Requires aws and jq on PATH. `make doctor` checks for both.
# ---------------------------------------------------------------------------

data "external" "knowledge_base" {
  program = ["bash", "-c", <<-EOT
    set -euo pipefail
    aws bedrock-agent get-knowledge-base \
      --knowledge-base-id ${var.knowledge_base_id} \
      --profile ${var.aws_profile} \
      --region ${var.aws_region} \
      --output json \
    | jq -c '{
        arn:    .knowledgeBase.knowledgeBaseArn,
        name:   .knowledgeBase.name,
        status: .knowledgeBase.status,
        type:  (.knowledgeBase.knowledgeBaseConfiguration.type // "UNKNOWN")
      }'
  EOT
  ]
}

# The data source it ingests from. Read separately because a MANAGED connector
# reports its bucket only inside an opaque connectorParameters JSON string -
# see local.kb_source_bucket_arns for how that is handled.
data "external" "knowledge_base_data_source" {
  program = ["bash", "-c", <<-EOT
    set -euo pipefail
    aws bedrock-agent get-data-source \
      --knowledge-base-id ${var.knowledge_base_id} \
      --data-source-id ${var.knowledge_base_data_source_id} \
      --profile ${var.aws_profile} \
      --region ${var.aws_region} \
      --output json \
    | jq -c '{
        name:   .dataSource.name,
        status: .dataSource.status,
        bucket_arn: (
          .dataSource.dataSourceConfiguration.s3Configuration.bucketArn
          // ( .dataSource.dataSourceConfiguration
               .managedKnowledgeBaseConnectorConfiguration.connectorParameters
               // "{}" | fromjson | .connectionConfiguration.bucketArn )
          // ""
        )
      }'
  EOT
  ]
}
