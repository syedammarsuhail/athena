package athena.tools

import rego.v1

default decision := {"allow": false, "reason": "no rule matched (default deny)"}

# restart_deployment
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

# scale_deployment
decision := {"allow": true, "reason": sprintf("scale to %d within bounds", [input.args.replicas])} if {
    input.tool == "k8s.scale_deployment"
    allowed_ns[input.args.namespace]
    input.args.replicas >= 1
    input.args.replicas <= 20
    input.args.replicas <= object.get(input.args, "current_replicas", 1) * 2
}

decision := {"allow": false, "reason": sprintf("scale request %d exceeds 2x current (%v) or 20 absolute",
                                                [input.args.replicas, input.args.current_replicas])} if {
    input.tool == "k8s.scale_deployment"
    allowed_ns[input.args.namespace]
    not scale_within_bounds
}

scale_within_bounds if {
    input.args.replicas >= 1
    input.args.replicas <= 20
    input.args.replicas <= object.get(input.args, "current_replicas", 1) * 2
}

# rollback_argocd_app
decision := {"allow": true, "reason": "rollback of a recent change is allowed (HITL still required)"} if {
    input.tool == "k8s.rollback_argocd_app"
    app_allowed[input.args.app_name]
}

decision := {"allow": false, "reason": "rollback of unknown app refused"} if {
    input.tool == "k8s.rollback_argocd_app"
    not app_allowed[input.args.app_name]
}

# cordon_node
decision := {"allow": true, "reason": "node may be cordoned"} if {
    input.tool == "k8s.cordon_node"
    not startswith(input.args.node_name, "ip-control")
}

# helpers + data
allowed_ns := {
    "online-boutique": true,
    "mlops": true,
    "agent": true,
}

app_allowed := {"online-boutique-dev", "agent-dev"}

recently_restarted(_, _) := false
