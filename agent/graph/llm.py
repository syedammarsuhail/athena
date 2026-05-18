"""
Thin wrapper around the LLM with metrics + retries.

Designed so you can swap the backend (Anthropic, OpenAI, Bedrock, Ollama) in
one place. All nodes import `chat()` from here.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import anthropic
from prometheus_client import Counter, Histogram

LLM_TOKENS = Counter("agent_llm_tokens_total", "tokens", ["node", "model", "kind"])
LLM_LATENCY = Histogram("agent_llm_seconds", "LLM call latency", ["node", "model"])
LLM_CALLS = Counter("agent_llm_calls_total", "LLM calls", ["node", "model", "result"])

# Use Bedrock if no direct API key is set
if os.getenv("ANTHROPIC_API_KEY"):
    _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
else:
    _client = anthropic.AnthropicBedrock(
        aws_region=os.getenv("AWS_REGION", "us-east-1"),
    )

MODEL = os.getenv("AGENT_MODEL", "claude-opus-4-7")
# Bedrock uses different model IDs
if isinstance(_client, anthropic.AnthropicBedrock):
    MODEL = os.getenv("AGENT_MODEL", "anthropic.claude-3-5-sonnet-20241022-v2:0")


def chat(*, node: str, system: str, messages: list[dict], tools: list[dict] | None = None,
         max_tokens: int = 2048) -> dict[str, Any]:
    """Call the LLM. Records metrics. Returns the raw response dict."""
    t0 = time.time()
    try:
        resp = _client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=tools or [],
        )
    except Exception:
        LLM_CALLS.labels(node, MODEL, "error").inc()
        raise
    LLM_LATENCY.labels(node, MODEL).observe(time.time() - t0)
    LLM_CALLS.labels(node, MODEL, "ok").inc()
    LLM_TOKENS.labels(node, MODEL, "input").inc(resp.usage.input_tokens)
    LLM_TOKENS.labels(node, MODEL, "output").inc(resp.usage.output_tokens)
    return resp.model_dump()


def extract_text(resp: dict) -> str:
    """Concatenate text blocks from a Messages-API response."""
    return "".join(b.get("text", "") for b in resp["content"] if b.get("type") == "text")


def extract_tool_uses(resp: dict) -> list[dict]:
    return [b for b in resp["content"] if b.get("type") == "tool_use"]


def parse_json_block(text: str) -> dict:
    """LLM output between ```json fences -> dict. Falls back to first {...}."""
    import re
    m = re.search(r"```json\s*(.+?)\s*```", text, re.S)
    raw = m.group(1) if m else None
    if raw is None:
        # find first balanced { ... }
        start = text.find("{")
        if start < 0:
            raise ValueError("no JSON in response")
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{": depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    raw = text[start:i+1]
                    break
    if raw is None:
        raise ValueError("unbalanced JSON")
    return json.loads(raw)
