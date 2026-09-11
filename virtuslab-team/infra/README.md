# BFE Knowledge Gateway — infrastructure

Terraform for the **Open Energy Knowledge Gateway** challenge (BFE, Energy Data
Hackdays, Brugg-Windisch). One `make apply` stands the stack up in either the
VirtusLab sandbox account or the BFE sandbox account handed over at the event.

**The knowledge base is not managed here.** BFE owns it; this stack reads it
through a data source and builds everything around it — IAM, buckets, the MCP
gateway, the AgentCore runtime, and cost guardrails.

> **Looking for the commands?** [`RUNBOOK.md`](RUNBOOK.md) is the
> copy-pasteable command reference — setup, the safe test loop, event day,
> recovery and troubleshooting. This file explains *why*; the runbook is *how*.

---

## Getting started

```bash
cd projects/bfe-knowledge-gateway/infra

make doctor          # what is missing
make deps            # install it
make login           # aws sso login
make bootstrap       # create the shared state bucket - ONCE, ever, by one person
make init
make plan  ENV=sandbox
make apply ENV=sandbox
```

`make help` lists every target.

### If `make` itself fails

On a Mac where `xcode-select -p` points at a broken or outdated Xcode,
`/usr/bin/make` is only a shim and dies before running anything:

```
sh: line 1: Abort trap: 6   /Applications/Xcode.app/.../xcodebuild -sdk '' -find make
make: error: ... failed with exit code 34304
xcode-select: Failed to locate 'make', requesting installation of command line developer tools
```

Nothing is wrong with `make` or with this repo — a working GNU Make already
sits at `/Library/Developer/CommandLineTools/usr/bin/make`; the shim just
cannot find it. Pick one fix:

```bash
# no sudo - redirect the shim for your shell, and persist it
export DEVELOPER_DIR=/Library/Developer/CommandLineTools
echo 'export DEVELOPER_DIR=/Library/Developer/CommandLineTools' >> ~/.zshrc

# or, system-wide and permanent (needs sudo)
sudo xcode-select --switch /Library/Developer/CommandLineTools
```

Do **not** accept the "install command line developer tools" prompt — they are
already installed. Verify with `make --version`, which should print GNU Make.

### Why `tenv` and not `brew install terraform`

`terraform` was removed from homebrew-core after the BUSL licence change, and
`hashicorp/tap` builds from source — which fails outright on an outdated Xcode.
`tenv` is bottled, installs cleanly, and reads `.terraform-version` (pinned to
`1.16.1`), so the whole team runs the same binary. `make deps` does this.

---

## Environments

`ENV` selects the tfvars file **and** the Terraform workspace, so sandbox values
can never be applied against BFE's account.

| `ENV` | Account | tfvars | Notes |
|---|---|---|---|
| `test` | `790954698087` | `envs/test.tfvars` | Disposable. Creates nothing that already exists, adopts nothing |
| `sandbox` | `790954698087` | `envs/sandbox.tfvars` | KB `MQJ3YJI3UB`, adopts the two existing buckets |
| `bfe` | `542202863496` | `envs/bfe.tfvars` | KB `ZPVWAEHXNB`, **`eu-central-1`**, shared account — see §"The BFE account" |

### Try it safely first: `ENV=test`

```bash
make plan    ENV=test                              # read it
make audit   ENV=test EXPECT_ACCOUNT=790954698087  # machine-check it
make apply   ENV=test                              # re-audits, then applies
make output  ENV=test
make destroy ENV=test                              # gone
```

`make audit` is a fail-closed check on the saved plan, and `make apply`
depends on it, so it cannot be skipped by accident. It refuses any plan that
manages a Neptune resource, manages the knowledge base instead of reading it,
touches anything Control Tower owns, or names a resource `*controltower*` /
`*stackset*` / `*neptune*`. It also prints every create, update, destroy and
import so a surprise takeover is visible before it happens.

`test` runs in the same account as `sandbox` but shares nothing with it:

- **Separate state** — the workspace name prefixes the state key.
- **Separate names** — every resource is `${project}-${environment}-*`, so
  `bfe-kg-test-gateway` cannot collide with `bfe-kg-sandbox-gateway`.
- **Adopts nothing** — `adopt_existing_buckets = {}`, so no live resource is
  taken over. Its buckets are new and empty, which is why they destroy cleanly.
- **Cannot harm the knowledge base** — it is a `data` source. Reads only.
- **No budget** — a second budget would only re-send SNS confirmation emails.

To exercise the billable half:

```bash
make plan ENV=test EXTRA='-var enable_agentcore=true'   # + Cognito + gateway
```

The AgentCore *runtime* additionally needs
`-var agent_runtime_container_uri=<ecr-uri>`; the gateway and Cognito pool
stand up without an image, which is enough to prove the wiring.

`envs/bfe.tfvars` ships with a deliberately invalid knowledge base id. The
variable is validated and the data source is a hard dependency, so a `plan`
fails immediately rather than building a stack wired to a KB that does not
exist. Discover the real values with:

```bash
aws bedrock-agent list-knowledge-bases --profile bfe --region eu-central-1
aws bedrock-agent list-data-sources --knowledge-base-id <id> \
  --profile bfe --region eu-central-1
```

Both `envs/bfe.tfvars` values were filled in from this on 2026-09-10. The
`--region` matters: the profile defaults to `eu-central-1`, but omitting it
elsewhere in the account returns an empty list rather than an error.

---

## State

One versioned, encrypted, TLS-only bucket: `bfe-kg-tfstate-790954698087`, in the
VirtusLab sandbox account so the whole team can reach it with the SSO access
they already have — **including** when the provider is pointed at BFE's account.
The Makefile passes the backend profile separately from `var.aws_profile`.

Locking is the **S3-native lockfile** (`use_lockfile = true`), GA since
Terraform 1.11. No DynamoDB table exists or is needed; the `dynamodb_table`
argument is deprecated.

`bootstrap/bootstrap-state.sh` creates the bucket. It is idempotent — every step
checks before it writes, so re-running is a no-op. It is not Terraform because
the state backend cannot be managed by the state it stores.

### Rollback

Versioning is what makes state recoverable:

```bash
make state-versions ENV=sandbox                    # newest first
make state-rollback ENV=sandbox VERSION=<id>
make plan ENV=sandbox                              # read the diff carefully
```

Noncurrent versions expire after 90 days.

### Stuck lock

A crashed apply leaves a `.tflock` object behind and the next run blocks. The
error prints an ID:

```bash
make unlock LOCK_ID=<id>
```

---

## What the gateway records

With `enable_gateway_observability = true`, the gateway delivers its MCP
`APPLICATION_LOGS` to CloudWatch Logs and to the `questions` bucket. Each
record carries `body.requestBody` — the whole JSON-RPC envelope, tool
arguments included — and a matching `body.responseBody`. That is where the
questions people ask are, and the only place they appear: the `TRACES` spans
carry `tool.name`, `latency_ms` and status codes but never the arguments.

Two consequences worth knowing before pointing this at anything:

- **Responses are captured too**, so the archive holds retrieved knowledge-base
  passages, not just questions. Fine for a public energy corpus.
- **There is no caller identity** in the documented record — `resource_arn`,
  `account_id`, `request_id`, `trace_id`, `span_id` and nothing that names who
  asked.

Reading it back, the SCP that blocks X-Ray, and the check that catches a
mis-encrypted archive are all in `RUNBOOK.md` section 5b. The dashboard URL is
`terraform output gateway_dashboard_url`.

---

## Cost

The account's `$200` budget was **74% consumed before this stack existed** —
$97.91 of it Neptune Analytics, in three days. Neptune is excluded by decision
and must not come back; see `.claude/rules/terraform.md`.

Anything that bills is behind an `enable_*` flag defaulting to `false`:

| Flag | Creates |
|---|---|
| `enable_lambda` | MCP tool Lambda + dependency layer |
| `enable_agentcore` | AgentCore runtime, gateway, Cognito pool |

IAM and buckets are always created — IAM is free, and it was the painful part of
the Console setup.

**To stop the billing at the end of a session, use `make down`, not
`make destroy`:**

```bash
make down ENV=sandbox     # removes AgentCore + Lambda, keeps storage and IAM
```

Buckets set `force_destroy = false`, which means Terraform **cannot** delete one
that still holds objects — a `destroy` against the sandbox fails with
`BucketNotEmpty` rather than taking the KB corpus with it. Empty test buckets
still destroy cleanly, which is what makes `ENV=test` disposable. Never flip
`force_destroy` to `true` to force a destroy through; empty the bucket on
purpose instead.

`guardrails.tf` adds the budget alarm that was missing: 50% and 80% on actual
spend, 100% on **forecast**, to both SNS and email.

---

## Importing existing resources

Everything lives in the root module on purpose — config-driven import cannot
target a resource inside a module ([hashicorp/terraform#35596][35596]).

To adopt a bucket, name it in `envs/<env>.tfvars`:

```hcl
adopt_existing_buckets = {
  artifacts = "agent-skills-energy-days"
}
```

then `make plan` — it should say *will be imported*, not *will be created*.
Remove the entry once state has settled.

For anything not already modelled, `imports.tf` has commented templates, and:

```bash
make import-scan ENV=sandbox     # writes generated_resources.tf
```

Two upstream limitations to expect: generation is still flagged experimental and
emits non-idiomatic config, and it struggles with discriminated-union blocks —
so the AgentCore `authorizer_configuration` / `target_configuration` output
needs hand-editing. `generated_resources.tf` is gitignored; treat it as scratch.

---

## Housekeeping

`make orphans` is a **read-only** report of leftovers from the Console-era
prototype — Neptune notebook roles, unattached policies, SageMaker lifecycle
configs, log groups for deleted knowledge bases. It deletes nothing; review and
remove by hand.

[35596]: https://github.com/hashicorp/terraform/issues/35596
