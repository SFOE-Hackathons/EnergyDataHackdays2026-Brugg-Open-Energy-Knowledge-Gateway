# Runbook

Every command, copy-pasteable. Run all of them from
`projects/bfe-knowledge-gateway/infra`.

`README.md` explains *why*; this file is only *how*.

Conventions used below:

- `ENV` is one of `test` (disposable), `sandbox` (`790954698087`), `bfe` (the
  event account). It selects both the tfvars file and the Terraform workspace.
- Anything marked **read-only** makes no change to AWS.

---

## 1. First time on a new machine

```bash
make doctor          # read-only: reports everything missing at once
make deps            # installs tenv + terraform, jq, awscli via brew
make login           # aws sso login
make init            # terraform init against the shared S3 backend
```

`make bootstrap` creates the shared state bucket. It has **already been run**
and is idempotent — you do not need it unless you are starting a brand new
account.

---

## 2. Start of every session

```bash
make login                    # SSO tokens last ~8h, so most mornings
make doctor                   # read-only
```

---

## 3. The safe test loop — do this before touching anything else

`ENV=test` is disposable: separate state, separate names (`bfe-kg-test-*`),
adopts nothing, and cannot modify the knowledge base.

```bash
# preflight — read-only
aws sts get-caller-identity --profile bedrock-kb --query Account --output text   # expect 790954698087
terraform workspace show
make validate                                                                     # read-only

# plan and machine-check it — read-only
make plan  ENV=test
make audit ENV=test EXPECT_ACCOUNT=790954698087

# apply (re-runs the audit first and refuses if it fails)
make apply  ENV=test
make output ENV=test

# verify what actually exists
aws iam list-roles --profile bedrock-kb \
  --query "Roles[?starts_with(RoleName,'bfe-kg-test')].RoleName" --output table
aws s3api get-bucket-versioning --bucket bfe-kg-test-graph --profile bedrock-kb
terraform plan -var-file=envs/test.tfvars -detailed-exitcode    # exit 0 = no drift
make state-versions ENV=test

# tear down and confirm
make destroy ENV=test
terraform state list                                            # expect empty
aws iam list-roles --profile bedrock-kb \
  --query "Roles[?starts_with(RoleName,'bfe-kg-test')].RoleName" --output text   # expect empty
```

### Include the billable half

Off by default. The gateway and Cognito pool stand up without a container
image, which is enough to prove the wiring:

```bash
make plan  ENV=test EXTRA='-var enable_agentcore=true'
make audit ENV=test EXPECT_ACCOUNT=790954698087
make apply ENV=test
```

The AgentCore **runtime** additionally needs an image:

```bash
make plan ENV=test EXTRA='-var enable_agentcore=true -var agent_runtime_container_uri=<ecr-uri>'
```

Always finish with `make destroy ENV=test`.

---

## 4. Sandbox

Same commands, different `ENV`. Note this environment **adopts two existing
buckets**, so the audit will report 2 imports — that is expected.

```bash
make plan  ENV=sandbox
make audit ENV=sandbox EXPECT_ACCOUNT=790954698087
make apply ENV=sandbox
make output ENV=sandbox
```

Stop the billing without destroying storage:

```bash
make down ENV=sandbox        # removes AgentCore + Lambda, keeps buckets and IAM
```

---

## 5. Event day: the BFE account

Account `542202863496`, region **`eu-central-1`**. `envs/bfe.tfvars` is filled
in and verified — no placeholders left.

The `bfe` profile is **static access keys**, not SSO. `make login` cannot
refresh it; if it stops working the key was revoked and BFE must issue another.

```bash
# 1. confirm you are pointed at BFE, not the sandbox
aws sts get-caller-identity --profile bfe --query Account --output text
# -> 542202863496

# 2. doctor now checks both profiles: bedrock-kb (state) and bfe (target)
make doctor ENV=bfe

# 3. usual loop, with the account assertion set to BFE's id
make plan  ENV=bfe
make audit ENV=bfe EXPECT_ACCOUNT=542202863496
make apply ENV=bfe
```

Re-discover the KB values if BFE rebuilds anything:

```bash
aws bedrock-agent list-knowledge-bases --profile bfe --region eu-central-1
aws bedrock-agent list-data-sources --knowledge-base-id <kb-id> \
  --profile bfe --region eu-central-1
```

### What BFE already built — do not collide with it

The account is **shared**: `user01`–`user12` all hold `AdministratorAccess`.
A working prototype is already running in `eu-central-1`, none of it managed
by this stack:

| Resource | Id |
|---|---|
| Managed KB | `ZPVWAEHXNB` — *KB-bfe-public* |
| Data source | `B1NDHNS8TN` — corpus `sandbox-bfe-public-data-pdf` |
| AgentCore gateway | `sandbox-bfe-public-kb-8thmswsvit` (MCP, Cognito JWT) |
| AgentCore runtime | `harness_harness_public_bfe` |
| Cognito pool | `eu-central-1_nbg0gHsbd` |

`enable_agentcore = false` for this env, so nothing collides today. Before
flipping it on, decide whether to **import** the existing gateway or create a
second one under the `bfe-kg-bfe-*` prefix — creating a duplicate silently is
the failure to avoid, since other people may already point clients at the
live URL.

### The SCP: what BFE's org denies

An org-level service control policy
(`arn:aws:organizations::429128461717:policy/o-3r6tcb49g2/.../p-bvj3x4lc`)
denies services account-wide. An SCP outranks IAM, so `AdministratorAccess`
does not help and nothing inside `542202863496` can lift it. `user02` cannot
read the policy either, so the deny list is only discoverable by hitting it.

| Denied | Consequence |
|---|---|
| `cloudformation:*` (incl. Cloud Control `GetResource`) | `hashicorp/awscc` cannot read anything — hence the CLI reads in `data.tf` |
| `SNS:CreateTopic`, `SNS:ListTopics` | no budget alert topic — `budget_notification_emails = []` for this env |

Expect more. Before enabling `agentcore` or `lambda` here, apply into
`ENV=test` first, or expect a mid-apply 403 with an `explicit deny in a
service control policy` message naming the same policy id. A partial apply is
recoverable — Terraform keeps what succeeded and re-converges on the next run.

`make destroy ENV=bfe` is blocked on purpose — it is a live shared account.

---

## 5b. Reading the questions people ask

`enable_gateway_observability` delivers the gateway's `APPLICATION_LOGS` to
CloudWatch Logs and to the `questions` bucket. The question text is in
`body.requestBody` and nowhere else.

```bash
# is anything arriving at all?
aws logs tail /aws/vendedlogs/bedrock-agentcore/gateway/vl-bfe-kg-bfe \
  --since 30m --profile bfe --region eu-central-1

# all three delivery pieces must exist and reference each other - two out of
# three delivers nothing and reports nothing
aws logs describe-delivery-sources      --profile bfe --region eu-central-1
aws logs describe-delivery-destinations --profile bfe --region eu-central-1
aws logs describe-deliveries            --profile bfe --region eu-central-1

# the archive must be READABLE, not merely present. This is the check that
# catches the wrong-KMS-key failure: AWS delivers logs encrypted with an
# AWS-managed key "in an unreadable format" instead of denying the write.
aws s3 ls s3://vl-bfe-kg-bfe-questions/AWSLogs/ --recursive --profile bfe
aws s3 cp s3://vl-bfe-kg-bfe-questions/<key> - --profile bfe | gunzip | head
# prints JSON with a body.requestBody field -> correct
# prints binary noise                        -> wrong key, fix kms.tf
```

The saved Insights queries are in the CloudWatch console under **Queries**,
filed as `vl-bfe-kg-bfe/questions-asked`, `/questions-ranked` and `/failures`.
`body.requestBody` is a Java-map-style string, not JSON, so they extract with a
regex; the greedy `.*` between `arguments={` and `}}}` is deliberate and must
not be made non-greedy.

**X-Ray and CloudWatch Transaction Search are unavailable in this account.**
SCP `p-bvj3x4lc` — the same one that denies `cloudformation:*` and
`SNS:CreateTopic` — denies `xray:*` outright:

```bash
aws xray get-trace-segment-destination --profile bfe --region eu-central-1
# AccessDeniedException ... with an explicit deny in a service control policy
```

So the `TRACES` delivery path and the GenAI Observability page cannot be used
here. Nothing is lost for capturing questions: spans carry `tool.name`,
`latency_ms` and status codes but never the tool arguments. The ops half comes
from the `AWS/Bedrock-AgentCore` metrics, which are published with no setup at
all and are what the dashboard's metric widgets read.

Open the dashboard with `terraform output gateway_dashboard_url`.

---

## 6. State and recovery

```bash
# what state exists at all
aws s3 ls s3://bfe-kg-tfstate-790954698087/ --recursive --profile bedrock-kb

# what Terraform currently tracks
terraform state list

# recoverable versions, newest first
make state-versions ENV=test

# roll back to a prior version, then READ THE DIFF before applying anything
make state-rollback ENV=test VERSION=<version-id>
make plan ENV=test
```

---

## 7. Adopting resources that already exist

```bash
# name the live resource in envs/<env>.tfvars under adopt_existing_buckets, then
make plan ENV=sandbox        # should say "will be imported", not "will be created"
make apply ENV=sandbox

# for anything not already modelled
make import-scan ENV=sandbox # writes generated_resources.tf (gitignored scratch)
```

---

## 8. Troubleshooting

### `make` aborts with `xcodebuild ... Abort trap: 6`

The `/usr/bin/make` shim cannot find the real make because `xcode-select`
points at a broken Xcode. Do **not** accept the "install command line
developer tools" prompt; they are already installed.

```bash
export DEVELOPER_DIR=/Library/Developer/CommandLineTools
echo 'export DEVELOPER_DIR=/Library/Developer/CommandLineTools' >> ~/.zshrc
# or, system-wide:  sudo xcode-select --switch /Library/Developer/CommandLineTools
make --version               # should print GNU Make
```

### `InvalidGrantException` / `No valid credential sources found`

The SSO token expired. They last about 8 hours.

```bash
make login
```

### `Error acquiring the state lock`

A previous run crashed and left a lock object. The error prints an ID.

```bash
make unlock LOCK_ID=<id-from-the-error>
```

Do not delete the state object to clear a lock.

### `BucketNotEmpty` during destroy

Working as intended — Terraform refuses to delete a bucket that still holds
objects, which is what protects the KB corpus and the knowledge graph. Empty
the bucket deliberately if you really mean it. Never set `force_destroy = true`
to force it through.

### Plan wants to create something that already exists

Adopt it instead — see section 7.

---

## 9. Housekeeping

```bash
make fmt                     # rewrite to canonical format
make validate                # read-only: fmt check + schema validate
make orphans                 # read-only report of Console-era leftovers
make clean                   # remove local plan files and generated config
make help                    # every target
```

`make orphans` deletes nothing. Review its output and remove by hand.

---

## 10. Read-only commands, for reference

Safe to run at any time, against any environment:

```bash
make doctor
make validate
make plan ENV=<env>
make audit ENV=<env>
make output ENV=<env>
make state-versions ENV=<env>
make orphans
make help
terraform state list
terraform workspace show
aws sts get-caller-identity --profile <profile>
```
