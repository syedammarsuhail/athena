# ADR-001: Multi-agent topology over a single LLM call

**Status:** Accepted
**Date:** Week 6

## Context

The agent needs to: classify an anomaly, investigate it (potentially many tool calls), pick a remediation, decide if approval is needed, execute, verify, and write a postmortem. The naive approach is one giant prompt: "Here's a situation; do everything."

## Decision

Decompose into a LangGraph state machine with explicit nodes — detect, diagnose, plan, hitl, execute, verify, report — each with its own:
- system prompt (narrow scope)
- tool subset (only what the node needs)
- output schema (so the next node can rely on it)
- metrics (latency, tokens, success rate per node)

## Consequences

### Positive
- **Observability:** we know which step failed, how long it took, and what it cost — per incident.
- **Cost control:** the detector uses ~400 tokens; the diagnostician uses 5–10k. Without separation, every event would pay the full price.
- **Testability:** each node is a pure-ish function; we mock the LLM and assert.
- **Safety:** the trust boundary is explicit — read tools in diagnose, write tools only via execute, both gated by OPA.
- **Iterability:** improving the diagnostician's prompt doesn't risk regressing the reporter.

### Negative
- **More code:** ~5x more Python than a single prompt would need.
- **State management:** we own merging partial states across nodes (LangGraph's reducers help, but the abstraction has a learning curve).
- **Latency stacking:** each LLM call adds 1–3s. We accept this; humans take minutes for the equivalent.

## Alternatives considered

- **Single ReAct loop:** simpler, but conflates reasoning with planning and gives no per-step metrics.
- **Agentic framework (CrewAI, AutoGen):** higher abstraction, but harder to inspect and version control. LangGraph's explicit graph fit the "verifiable behavior" requirement better.
