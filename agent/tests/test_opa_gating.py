"""Test the actual rego policy by running it against an OPA binary in CI.
For unit tests we patch check_policy directly; for integration, you can run:

    opa eval -d agent/policies/tools.rego \
      -i agent/tests/fixtures/opa_input_restart.json \
      'data.athena.tools.decision'
"""
import json
from agent.graph.mcp_client import WRITE_TOOLS


def test_write_tools_set_matches_executor_actions():
    """If we add an action to the executor, it must be in WRITE_TOOLS so
    OPA gates it. This guards against forgetting to gate a new tool."""
    from agent.graph.nodes.executor import ACTION_TO_TOOL
    for action, (tool, _) in ACTION_TO_TOOL.items():
        if action in ("no_action", "escalate_human"):
            continue
        assert tool in WRITE_TOOLS, f"executor action {action} uses {tool} which is NOT in WRITE_TOOLS"
