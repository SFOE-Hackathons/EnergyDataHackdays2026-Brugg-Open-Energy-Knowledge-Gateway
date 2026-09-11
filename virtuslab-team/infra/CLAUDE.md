# bfe-knowledge-gateway/infra

Terraform for the BFE **Open Energy Knowledge Gateway** challenge
(Energy Data Hackdays, Brugg-Windisch). It stands up the MCP gateway, the
AgentCore runtime, IAM and buckets in either the VirtusLab sandbox account
(`790954698087`) or the BFE sandbox account BFE hands over at the event.

The repository-wide rules in `../../../.claude/rules/git-workflow.md` still
apply here in full — branch naming, imperative commit subjects, and **no
attribution trailers**.

## Rules

@.claude/rules/terraform.md

## Quick reference

- Commands live in `RUNBOOK.md`. Prefer citing a runbook section over
  inventing an ad-hoc command; add new ones there.
- `make doctor` before anything else; `make deps` fixes what it finds.
- Terraform `>= 1.11`, pinned to 1.16.1 in `.terraform-version` (use `tenv`).
- `ENV` selects both the tfvars file and the workspace: `make plan ENV=bfe`.
- **Never** turn the knowledge base into a resource — it is BFE's, read-only.
- **Never** manage anything Control Tower owns (VPCs, Config, `aws-controltower-*`).
- **Never** add a Neptune Analytics resource.
- `make apply` only ever applies a saved plan file.
