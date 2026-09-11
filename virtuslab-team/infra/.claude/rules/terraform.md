# Terraform rules for this stack

Binding for every change under `projects/bfe-knowledge-gateway/infra/`.

---

## 1. What this stack may and may not own

**Never manage the Bedrock Knowledge Base.** It belongs to BFE. It is read
through `data.external.knowledge_base` and
`data.external.knowledge_base_data_source` in `data.tf`, which shell out to
`aws bedrock-agent get-*`. It used to go through `hashicorp/awscc`; BFE's
organisation denies `cloudformation:GetResource` — the namespace Cloud Control
authorises under — in an SCP, so that provider cannot read anything in
`542202863496`. Do not reintroduce it. Do not add
`aws_bedrockagent_knowledge_base` or `aws_bedrockagent_data_source`, and do not
write an import block for either. If the KB needs to change, that is a
conversation with the challenge owner, not a commit.

**Never manage anything AWS Control Tower owns.** The VPCs, subnets, security
groups, S3 gateway endpoints, Config recorders, flow-log groups, the Datadog
integration, and every `aws-controltower-*`, `AWSControlTowerExecution`,
`stacksets-exec-*` and `StackSet-*` role are provisioned by StackSets from the
org management account. Importing them makes Terraform fight the StackSet.

**Never add a Neptune Analytics resource.** The team decided against it. A
16 m-NCU graph costs roughly $700/month and three days of it consumed 74% of
this account's budget in September 2026. This is a standing decision, not a
preference.

Also out of scope: `AWSServiceRoleFor*` service-linked roles, and the
`AWSReservedSSO_*` roles.

---

## 2. Safety

- **Never `terraform apply` without a saved plan file.** `make apply` refuses
  to run without one. Never pass `-auto-approve`.
- **`make apply` depends on `make audit`; never bypass it** by calling
  `terraform apply` directly. The audit is fail-closed and encodes the
  prohibitions in section 1 as an executable check. If a new resource type
  must never be managed here, add it to `FORBIDDEN_TYPES` in
  `audit-plan.py` in the same commit that introduces the rule.
- **Never `terraform destroy` against `ENV=bfe` without saying so out loud
  first.** It is a shared account during a live event.
- **To stop billing, run `make down`, not `make destroy`.** `down` flips the
  `enable_*` flags off and applies, removing only what bills.
- **Never set `force_destroy = true` on a bucket to get a destroy through.**
  Terraform refusing to delete a bucket that still holds objects is the guard
  that protects the KB corpus and the generated knowledge graph. If a bucket
  genuinely must go, empty it deliberately first.
- **Test in `ENV=test` before `ENV=sandbox` or `ENV=bfe`.** It is a disposable
  workspace in the same account, sharing no state and no resource names.
- Read `terraform plan` output before applying. A `destroy` or `replace` on a
  bucket, a Cognito pool or an AgentCore runtime is data loss.
- If a lock is stuck, `make unlock LOCK_ID=...`. Do not delete the state object.
- Rolling back state is `make state-versions` then `make state-rollback
  VERSION=...`. That is what bucket versioning is for. Never hand-edit state.

---

## 3. Secrets and state

- **Never commit** `*.tfstate`, `*.tfvars.local`, `generated_resources.tf`, or a
  plan file. All are gitignored; do not `git add -f` them.
- **Never put a secret in a `.tfvars`.** The Cognito client secret is a
  Terraform output marked `sensitive`; the AgentCore OAuth2 credential is
  managed by AgentCore and never enters this config.
- `.terraform.lock.hcl` **is** committed on purpose: it pins provider versions
  for the whole team. Do not gitignore it.
- **After changing a provider version, re-lock for every platform**, not just
  your own. A plain `terraform init` records an `h1:` hash only for the machine
  that ran it, which makes the lock file rewrite itself on a teammate's
  different OS and fail outright under `-lockfile=readonly` in CI:

  ```bash
  terraform providers lock \
    -platform=darwin_arm64 -platform=darwin_amd64 \
    -platform=linux_amd64  -platform=linux_arm64
  ```

  Expect four `h1:` hashes per provider afterwards.
- State lives in `bfe-kg-tfstate-790954698087` in the VirtusLab sandbox, even
  when the provider targets BFE. The Makefile passes the backend profile
  separately from `var.aws_profile` for exactly this reason.

---

## 4. Style

- Everything stays in the **root module**. Config-driven import cannot target a
  resource inside a module (hashicorp/terraform#35596), and this stack needs
  import to work. Do not "tidy" it into `modules/`.
- Region is per-environment, set only in `envs/`. `sandbox` and `test` are
  `us-east-1`; `bfe` is **`eu-central-1`**, because that is where BFE built the
  knowledge base, the corpus bucket, the gateway and the Cognito pool. The
  `us-east-1` default in `variables.tf` and the `us-east-1` backend in
  `versions.tf` are both deliberate and unrelated — state always lives in the
  VirtusLab sandbox. The account is never hardcoded outside `envs/`.
- Derive ARNs from resources and data sources; never paste one in. The live
  Console-built policy has `"arn:aws:s3:::sfoe-data-energy-monitoring "` with a
  trailing space, so its bucket-level `ListBucket` silently never matches. That
  is the failure mode derivation prevents.
- Gate anything that bills behind an `enable_*` variable, defaulting to `false`.
  IAM is free and is always created — it was the painful part in the Console.
- Run `make validate` before committing.

---

## 5. Cost

The account's `$200` budget was 74% consumed before this stack existed. State
the cost impact of any new resource in the PR description. AgentCore runtimes
and gateways bill while they exist — `make destroy` when a working session ends.
