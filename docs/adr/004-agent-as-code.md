# ADR-004: Agent prompts, policies, and graph definitions in Git

**Status:** Accepted
**Date:** Week 7

## Context

Production AI systems drift when prompts live in chat UIs, policies in Slack threads, and graph topology in someone's notebook. We have a strong opinion: this is a software system, treat it like one.

## Decision

Everything that defines agent behavior is in this repo:
- `agent/prompts/*.md` — every system prompt
- `agent/policies/*.rego` — every OPA rule
- `agent/graph/graph.py` — the topology
- `agent/graph/nodes/*.py` — node logic

Changes go through PR + review. Production deploys via Argo CD when main is updated. Rollbacks via `git revert`.

## Consequences

### Positive
- **Reproducibility:** any historical version of the agent is `git checkout`-able.
- **Diffability:** prompt changes show up in PR reviews.
- **Compliance:** clear chain-of-custody for changes to a system that takes production actions.
- **Bisectable regressions:** if agent quality drops on a Tuesday, you can `git bisect` between the Monday and Tuesday commits.

### Negative
- **Friction for tweaking:** can't A/B-test a prompt in 30 seconds.
- **Mitigation:** add a `agent/prompts/experimental/` lane that doesn't require review, but those prompts can't be promoted to prod without PR.
