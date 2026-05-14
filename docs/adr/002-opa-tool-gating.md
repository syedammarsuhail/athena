# ADR-002: OPA between the LLM and kubectl

**Status:** Accepted
**Date:** Week 6

## Context

The agent can take destructive actions: restart deployments, scale them, rollback Argo apps, cordon nodes. We need a trust boundary that is robust against LLM hallucination, prompt injection (via log content), and bugs in our own code.

## Decision

Every write tool call passes through OPA before execution. The policy (`agent/policies/tools.rego`) decides allow/deny based on:
- tool name + structured args (not free-text)
- contextual data (current replicas, recent restart history)
- target namespace allow-list

The MCP server treats the LLM as untrusted input. The MCP server treats OPA as the authority. The MCP server also enforces a Redis-backed cooldown as defense-in-depth: even if OPA misjudges, a runaway agent can't loop on restart_deployment.

## Consequences

### Positive
- **Audit:** every decision is `(input, output, reason)` — replayable, regression-testable.
- **Bounded blast radius:** worst-case is the agent issues legal actions, not arbitrary ones.
- **Separation of concerns:** policy reviewers can read Rego without touching Python.
- **Promotable:** the same policy applies to humans calling the same MCP tools.

### Negative
- **One more hop:** ~5ms added per write tool call.
- **Rego learning curve:** team needs to learn it. (Worth it; it's used elsewhere in the org.)

## Alternatives considered

- **Trust the LLM:** rejected — single point of failure.
- **Hard-code policies in Python:** rejected — couples policy to code, slows policy changes.
- **Use Kyverno for everything:** Kyverno gates at the admission layer (after we've already constructed the request). OPA pre-decides; cleaner.
