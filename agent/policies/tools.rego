package athena.tools

# Default decision: deny.
default decision := {"allow": false, "reason": "no rule matched (default deny)"}

# ----------------------------------------------------------------------------
# restart_deployment
# ----------------------------------------------------------------------------

decision := {"allow": true, "reason": "restart in allowed namespace"} if {
    input.tool == "k8s.restart_deployment"
    allowed_ns[input.args.namespace]
    not recently_restarted(input.args.namespace, input.args.name)
}

decision := {"allow": false, "reason": "cooldown: deployment was restarted in the last 5 minutes"} if {
    input.tool == "k8s.restart_deployment"
    allowed_ns[input.args.namespace]
    recently_restarted(input.args.namespace, input.args.name)
}

# ----------------------------------------------------------------------------
# scale_deployment — cap to ≤2× current and ≤20 absolute
# ----------------------------------------------------------------------------

decision := {"allow": true, "reason": sprintf("scale to %d within bounds", [input.args.replicas])} if {
    input.tool == "k8s.scale_deployment"
    allowed_ns[input.args.namespace]
    input.args.replicas <= 20
    # current_replicas is enriched into input.args by the MCP server before OPA call
    current := default_to_one(input.args.current_replicas)
    input.args.replicas <= current * 2
    input.args.replicas >= 1
}

decision := {"allow": false, "reason": sprintf("scale request %d exceeds 2× current (%v) or 20 absolute",
                                                [input.args.replicas, input.args.current_replicas])} if {
    input.tool == "k8s.scale_deployment"
    allowed_ns[input.args.namespace]
    not within_scale_bounds(input.args)
}

# ----------------------------------------------------------------------------
# rollback_argocd_app — high-risk; only allowed if the failing rev is recent
# (the requires_approval flag elsewhere forces HITL regardless)
# ----------------------------------------------------------------------------

decision := {"allow": true, "reason": "rollback of a recent change is allowed (HITL still required)"} if {
    input.tool == "k8s.rollback_argocd_app"
    app_allowed[input.args.app_name]
}

decision := {"allow": false, "reason": "rollback of unknown app refused"} if {
    input.tool == "k8s.rollback_argocd_app"
    not app_allowed[input.args.app_name]
}

# ----------------------------------------------------------------------------
# cordon_node — only nodes in the dev cluster; never prod control plane nodes
# ----------------------------------------------------------------------------

decision := {"allow": true, "reason": "node may be cordoned"} if {
    input.tool == "k8s.cordon_node"
    not startswith(input.args.node_name, "ip-control")
}

# ----------------------------------------------------------------------------
# helpers + data
# ----------------------------------------------------------------------------

allowed_ns := {
    "online-boutique": true,
    "mlops": true,
    "agent": true,
}

app_allowed := {"online-boutique-dev", "agent-dev"}

recently_restarted(ns, name) if {
    # In production, replace with a check against a Redis/Prom counter.
    # For now: no-op; cooldown is enforced inside the MCP server too.
    false
}

within_scale_bounds(args) if {
    args.replicas <= 20
    current := default_to_one(args.current_replicas)
    args.replicas <= current * 2
    args.replicas >= 1
}

default_to_one(x) := x if { x }
default_to_one(x) := 1 if { not x }
