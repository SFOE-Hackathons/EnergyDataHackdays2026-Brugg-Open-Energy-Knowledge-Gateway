# Terraform driver for the BFE Knowledge Gateway infrastructure.
#
#   make doctor            what is missing on this machine
#   make deps              install it
#   make bootstrap         create the shared state bucket (once, ever)
#   make plan ENV=sandbox
#   make apply ENV=sandbox
#
# ENV selects both the tfvars file and the Terraform workspace, so sandbox
# values can never be applied against BFE's account.

ENV           ?= sandbox
STATE_PROFILE ?= bfe
TF_MIN         := 1.11
TF             := terraform

TFVARS := envs/$(ENV).tfvars
PLAN   := tfplan-$(ENV)

STATE_BUCKET ?= vl-bfe-kg-tfstate-542202863496
STATE_REGION ?= eu-central-1
STATE_KEY    := bfe-knowledge-gateway/terraform.tfstate
# Workspaces prefix the key; ENV is always a named workspace, never "default".
STATE_OBJECT := env:/$(ENV)/$(STATE_KEY)

AWS := aws --profile $(STATE_PROFILE) --region $(STATE_REGION) --no-cli-pager

# The account Terraform points the *provider* at, read out of the env's tfvars.
# For sandbox and test this is the same profile as the state; for bfe it is a
# second account reached with credentials the SSO login does not cover.
TARGET_PROFILE := $(shell sed -n 's/^[[:space:]]*aws_profile[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' $(TFVARS) 2>/dev/null)

# Optional: make audit assert this account id appears in the plan.
EXPECT_ACCOUNT ?=

.DEFAULT_GOAL := help
# Report-only targets print their findings, not the shell that produced them.
.SILENT: help doctor orphans state-versions
.PHONY: help doctor deps login bootstrap init workspace fmt validate plan audit apply \
        destroy down unlock output state-versions state-rollback import-scan orphans clean

help: ## Show this help
	grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
	| awk -F':.*?## ' '{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# -----------------------------------------------------------------------------
# Prerequisites
# -----------------------------------------------------------------------------

doctor: ## Report every missing prerequisite at once
	fail=0; \
	if command -v terraform >/dev/null 2>&1; then \
	  have=$$(terraform version -json 2>/dev/null | sed -n 's/.*"terraform_version"[: ]*"\([^"]*\)".*/\1/p'); \
	  lowest=$$(printf '%s\n%s\n' "$(TF_MIN)" "$$have" | sort -V | head -1); \
	  if [ "$$lowest" = "$(TF_MIN)" ]; then \
	    echo "  ok   terraform $$have"; \
	  else \
	    echo "  FAIL terraform $$have is below $(TF_MIN) (needed for use_lockfile)"; fail=1; \
	  fi; \
	else echo "  FAIL terraform not installed"; fail=1; fi; \
	if command -v aws >/dev/null 2>&1; then \
	  if aws bedrock-agentcore-control help >/dev/null 2>&1; then \
	    echo "  ok   aws cli $$(aws --version 2>&1 | cut -d' ' -f1)"; \
	  else \
	    echo "  FAIL aws cli has no bedrock-agentcore-control service - too old"; fail=1; \
	  fi; \
	else echo "  FAIL aws cli not installed"; fail=1; fi; \
	command -v jq >/dev/null 2>&1 && echo "  ok   jq" || { echo "  FAIL jq not installed"; fail=1; }; \
	if $(AWS) sts get-caller-identity >/dev/null 2>&1; then \
	  echo "  ok   sso session for $(STATE_PROFILE) (state)"; \
	else \
	  echo "  FAIL no valid session for $(STATE_PROFILE) - run: make login"; fail=1; \
	fi; \
	if [ -z "$(TARGET_PROFILE)" ]; then \
	  echo "  FAIL no aws_profile in $(TFVARS)"; fail=1; \
	elif [ "$(TARGET_PROFILE)" != "$(STATE_PROFILE)" ]; then \
	  if aws --profile $(TARGET_PROFILE) --no-cli-pager sts get-caller-identity >/dev/null 2>&1; then \
	    echo "  ok   credentials for $(TARGET_PROFILE) (target, ENV=$(ENV))"; \
	  elif aws configure get sso_session --profile $(TARGET_PROFILE) >/dev/null 2>&1; then \
	    echo "  FAIL no valid session for $(TARGET_PROFILE) - run: make login ENV=$(ENV)"; fail=1; \
	  else \
	    echo "  FAIL no working credentials for $(TARGET_PROFILE) - static keys unset, expired or revoked"; fail=1; \
	  fi; \
	fi; \
	[ $$fail -eq 0 ] || { echo; echo "Run 'make deps' to fix what can be installed."; exit 1; }

deps: ## Install prerequisites via brew
	# terraform is NOT in homebrew-core (removed after the BUSL licence change)
	# and hashicorp/tap builds from source, which fails on an outdated Xcode.
	# tenv is bottled, installs cleanly, and pins the version via .terraform-version.
	command -v tenv >/dev/null 2>&1 || brew install tenv
	tenv tf install
	command -v jq >/dev/null 2>&1 || brew install jq
	# awscli here came from the Intel brew at /usr/local; installing it on the
	# primary (arm) brew shadows the stale one, since /opt/homebrew/bin wins in PATH.
	aws bedrock-agentcore-control help >/dev/null 2>&1 || brew install awscli || brew upgrade awscli
	$(MAKE) doctor

login: ## Refresh the AWS SSO session(s) for ENV
	# State now lives in the BFE account, which uses static keys - there is
	# usually nothing to refresh. Only run sso login on profiles that are SSO.
	@if aws configure get sso_session --profile $(STATE_PROFILE) >/dev/null 2>&1; then \
	  aws sso login --profile $(STATE_PROFILE); \
	else \
	  echo "note: $(STATE_PROFILE) (state) uses static keys - nothing to refresh."; \
	fi
	# The target account may be a second SSO instance, or static keys that no
	# login can refresh. Only the former is actionable here.
	@if [ -n "$(TARGET_PROFILE)" ] && [ "$(TARGET_PROFILE)" != "$(STATE_PROFILE)" ]; then \
	  if aws configure get sso_session --profile $(TARGET_PROFILE) >/dev/null 2>&1; then \
	    aws sso login --profile $(TARGET_PROFILE); \
	  else \
	    echo "note: $(TARGET_PROFILE) uses static keys - nothing to refresh."; \
	    echo "      if it fails, the key was revoked; get a new one from BFE."; \
	  fi; \
	fi

# -----------------------------------------------------------------------------
# State
# -----------------------------------------------------------------------------

bootstrap: ## Create the shared, versioned state bucket (idempotent)
	./bootstrap/bootstrap-state.sh $(STATE_PROFILE) $(STATE_REGION)

init: ## terraform init against the shared backend
	$(TF) init -input=false -backend-config="profile=$(STATE_PROFILE)"

workspace: ## Select (or create) the workspace for $(ENV)
	$(TF) workspace select -or-create $(ENV)

# -----------------------------------------------------------------------------
# Plan / apply
# -----------------------------------------------------------------------------

fmt: ## Rewrite files to canonical format
	$(TF) fmt -recursive

validate: ## Check syntax and provider schemas
	$(TF) fmt -check -recursive
	$(TF) validate

plan: workspace ## Plan into $(PLAN). Add flags with EXTRA='-var foo=bar'
	$(TF) plan -input=false -var-file=$(TFVARS) $(EXTRA) -out=$(PLAN)

audit: ## Safety-audit the saved plan before applying it
	test -f $(PLAN) || { echo "no $(PLAN); run 'make plan ENV=$(ENV)' first"; exit 1; }
	$(TF) show -json $(PLAN) | python3 audit-plan.py $(EXPECT_ACCOUNT)

apply: workspace audit ## Audit, then apply the saved plan - never a bare apply
	test -f $(PLAN) || { echo "no $(PLAN); run 'make plan ENV=$(ENV)' first"; exit 1; }
	$(TF) apply -input=false $(PLAN)
	rm -f $(PLAN)

down: workspace ## Tear down everything that bills, keep storage and IAM
	$(TF) apply -input=false -var-file=$(TFVARS) -var enable_agentcore=false -var enable_lambda=false

destroy: workspace ## Full teardown. Fails on non-empty buckets, by design
	@test "$(ENV)" != "bfe" || { echo "refusing: destroy ENV=bfe is a live shared account. Say so out loud first, then use terraform directly."; exit 1; }
	$(TF) plan -input=false -destroy -var-file=$(TFVARS) -out=$(PLAN).destroy
	$(TF) apply $(PLAN).destroy
	rm -f $(PLAN).destroy

output: workspace ## Show outputs
	$(TF) output

unlock: ## Release a stale lock left by a crashed apply: make unlock LOCK_ID=...
	test -n "$(LOCK_ID)" || { echo "usage: make unlock LOCK_ID=<id from the error>"; exit 1; }
	$(TF) force-unlock $(LOCK_ID)

# -----------------------------------------------------------------------------
# Rollback - this is what bucket versioning buys
# -----------------------------------------------------------------------------

state-versions: ## List recoverable state versions, newest first
	$(AWS) s3api list-object-versions --bucket $(STATE_BUCKET) --prefix "$(STATE_OBJECT)" \
	  --query 'reverse(sort_by(Versions,&LastModified))[].{When:LastModified,Id:VersionId,Latest:IsLatest,Size:Size}' \
	  --output table

state-rollback: ## Restore a prior state version: make state-rollback VERSION=<id>
	test -n "$(VERSION)" || { echo "usage: make state-rollback VERSION=<id from state-versions>"; exit 1; }
	$(AWS) s3api copy-object --bucket $(STATE_BUCKET) --key "$(STATE_OBJECT)" \
	  --copy-source "$(STATE_BUCKET)/$(STATE_OBJECT)?versionId=$(VERSION)" >/dev/null
	echo "restored $(VERSION); run 'make plan ENV=$(ENV)' and read the diff carefully"

# -----------------------------------------------------------------------------
# Import
# -----------------------------------------------------------------------------

import-scan: workspace ## Generate HCL for resources named in imports.tf
	$(TF) plan -input=false -var-file=$(TFVARS) -generate-config-out=generated_resources.tf
	echo "wrote generated_resources.tf (gitignored) - hand-edit before keeping"

# -----------------------------------------------------------------------------
# Housekeeping
# -----------------------------------------------------------------------------

orphans: ## Read-only report of leftovers to delete by hand
	echo "== IAM roles for deleted resources =="
	$(AWS) iam list-roles --query "Roles[?starts_with(RoleName,'AWSNeptuneNotebookRole') || RoleName=='AmazonBedrockExecutionRoleForKnowledgeBase_19mjs'].RoleName" --output table
	echo "== customer-managed policies attached to nothing =="
	$(AWS) iam list-policies --scope Local --query 'length(Policies[?AttachmentCount==`0`])' --output text
	echo "== SageMaker notebook lifecycle configs =="
	$(AWS) sagemaker list-notebook-instance-lifecycle-configs --region us-east-1 \
	  --query "NotebookInstanceLifecycleConfigs[?starts_with(NotebookInstanceLifecycleConfigName,'aws-neptune')].NotebookInstanceLifecycleConfigName" --output table
	echo "== log groups with no retention =="
	$(AWS) logs describe-log-groups --region us-east-1 \
	  --query 'logGroups[?retentionInDays==null].logGroupName' --output table
	echo
	echo "Nothing was deleted. Review, then remove by hand."

clean: ## Remove local plan files and generated config
	rm -f tfplan-* generated_resources.tf
