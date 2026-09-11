#!/usr/bin/env python3
"""Fail-closed safety audit of a saved Terraform plan.

Reads `terraform show -json <planfile>` on stdin and refuses anything this
stack must never do. Run it between `make plan` and `make apply`; `make audit`
wires that up.

Exit 0 only if every check passes. Any violation exits 1 and names it.
"""

import json
import sys

# Resources this stack must never manage. See .claude/rules/terraform.md.
FORBIDDEN_TYPES = {
    "aws_neptunegraph_graph": "Neptune Analytics - excluded by decision, ~$700/month",
    "aws_neptune_cluster": "Neptune - excluded by decision",
    "aws_neptune_cluster_instance": "Neptune - excluded by decision",
    "aws_bedrockagent_knowledge_base": "the knowledge base belongs to BFE; read it, never manage it",
    "aws_bedrockagent_data_source": "the KB data source belongs to BFE; read it, never manage it",
    "aws_vpc": "Control Tower owns the VPCs",
    "aws_subnet": "Control Tower owns the subnets",
    "aws_flow_log": "Control Tower owns flow logs",
    "aws_config_configuration_recorder": "Control Tower owns AWS Config",
    "aws_cloudformation_stack": "Control Tower StackSets are managed from the org account",
}

# Substrings that must not appear in the NAME of anything we create.
FORBIDDEN_NAME_PARTS = ("controltower", "aws-controltower", "stackset", "neptune")

NAME_KEYS = ("bucket", "name", "function_name", "role_name", "policy_name",
             "layer_name", "agent_runtime_name", "domain")


def resource_name(after):
    for k in NAME_KEYS:
        v = after.get(k)
        if isinstance(v, str) and v:
            return v
    return ""


def main():
    plan = json.load(sys.stdin)
    changes = plan.get("resource_changes", [])
    expect_account = sys.argv[1] if len(sys.argv) > 1 else None

    violations = []
    creates, updates, destroys, imports = [], [], [], []

    for r in changes:
        actions = r["change"]["actions"]
        if actions == ["no-op"]:
            continue
        after = r["change"].get("after") or {}
        addr, rtype = r["address"], r["type"]

        if rtype in FORBIDDEN_TYPES:
            violations.append(f"{addr} ({rtype}): {FORBIDDEN_TYPES[rtype]}")

        name = resource_name(after).lower()
        for part in FORBIDDEN_NAME_PARTS:
            if part in name:
                violations.append(f"{addr}: name {name!r} contains {part!r}")

        if r["change"].get("importing"):
            imports.append(addr)
        if "delete" in actions:
            destroys.append(addr)
        elif "create" in actions:
            creates.append((addr, resource_name(after)))
        elif "update" in actions:
            updates.append(addr)

    print(f"create   {len(creates)}")
    print(f"update   {len(updates)}")
    print(f"destroy  {len(destroys)}")
    print(f"import   {len(imports)}")

    if creates:
        print("\nwill create:")
        for addr, name in sorted(creates):
            print(f"  {name or addr}")
    if destroys:
        print("\nWILL DESTROY - read these carefully:")
        for a in destroys:
            print(f"  {a}")
    if imports:
        print("\nwill take over existing resources:")
        for a in imports:
            print(f"  {a}")

    # The knowledge base must be read-only: present as data, absent as resource.
    prior = (plan.get("prior_state", {}).get("values", {})
             .get("root_module", {}).get("resources", []))
    kb_data = [d["address"] for d in prior
               if d.get("mode") == "data" and "knowledge_base" in d["address"]]
    print(f"\nKB read as data source: {kb_data or 'NOT READ - check data.tf'}")
    if not kb_data:
        violations.append("the knowledge base is not being read as a data source")

    if expect_account:
        actual = (plan.get("configuration", {}) or {})
        found = json.dumps(plan).count(expect_account)
        print(f"expected account {expect_account} referenced {found}x")
        if found == 0:
            violations.append(
                f"expected account {expect_account} appears nowhere in the plan")

    if violations:
        print("\nFAILED - " + str(len(violations)) + " violation(s):")
        for v in violations:
            print(f"  !! {v}")
        return 1

    print("\nPASSED - no forbidden resource, no unexpected destroy path")
    return 0


if __name__ == "__main__":
    sys.exit(main())
