"""
Thin wrapper around the LLM with metrics + retries.

Backends (checked in order):
  1. GROQ_API_KEY        → Groq (free tier, Llama 3.3 70B)
  2. ANTHROPIC_API_KEY   → Anthropic direct API
  3. (neither)           → Anthropic Bedrock (needs AWS creds)

All nodes import chat() from here. Response is always Anthropic-format dict
so nodes never need to know which backend is active.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from opentelemetry import trace
from prometheus_client import Counter, Histogram

LLM_TOKENS = Counter("agent_llm_tokens_total", "tokens", ["node", "model", "kind"])
LLM_LATENCY = Histogram("agent_llm_seconds", "LLM call latency", ["node", "model"])
LLM_CALLS = Counter("agent_llm_calls_total", "LLM calls", ["node", "model", "result"])

_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
_RETRY_BASE_S = float(os.getenv("LLM_RETRY_BASE_S", "2"))
_tracer = trace.get_tracer("athena.llm")

# ── Backend selection ────────────────────────────────────────────────────────

_BACKEND: str  # "groq" | "anthropic" | "bedrock"

if os.getenv("GROQ_API_KEY"):
    from openai import OpenAI as _OpenAIClient
    _client = _OpenAIClient(
        api_key=os.getenv("GROQ_API_KEY"),
        base_url="https://api.groq.com/openai/v1",
    )
    _BACKEND = "groq"
    MODEL = os.getenv("AGENT_MODEL", "llama-3.3-70b-versatile")

elif os.getenv("ANTHROPIC_API_KEY"):
    import anthropic
    _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    _BACKEND = "anthropic"
    MODEL = os.getenv("AGENT_MODEL", "claude-haiku-4-5-20251001")

else:
    import anthropic
    _client = anthropic.AnthropicBedrock(
        aws_region=os.getenv("AWS_REGION", "us-east-1"),
    )
    _BACKEND = "bedrock"
    MODEL = os.getenv("AGENT_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")


# ── Groq format adapters ─────────────────────────────────────────────────────

def _tools_to_openai(tools: list[dict]) -> list[dict]:
    """Anthropic tool schema → OpenAI function-calling schema."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for t in tools
    ]


def _messages_to_openai(messages: list[dict]) -> list[dict]:
    """Convert Anthropic conversation history to OpenAI format."""
    result = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        if isinstance(content, str):
            result.append({"role": role, "content": content})

        elif isinstance(content, list):
            # User message containing tool_result blocks → OpenAI "tool" role messages
            if role == "user" and all(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content
            ):
                for block in content:
                    result.append({
                        "role": "tool",
                        "tool_call_id": block["tool_use_id"],
                        "content": str(block.get("content", "")),
                    })
            # Assistant message may have text + tool_use blocks
            elif role == "assistant":
                text_parts = [b for b in content if isinstance(b, dict) and b.get("type") == "text"]
                tool_uses = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
                oai: dict[str, Any] = {"role": "assistant", "content": None}
                if text_parts:
                    oai["content"] = " ".join(b.get("text", "") for b in text_parts)
                if tool_uses:
                    oai["tool_calls"] = [
                        {
                            "id": tu["id"],
                            "type": "function",
                            "function": {
                                "name": tu["name"],
                                "arguments": json.dumps(tu.get("input", {})),
                            },
                        }
                        for tu in tool_uses
                    ]
                result.append(oai)
            else:
                # Plain user message with text blocks
                text = " ".join(
                    b.get("text", "") if isinstance(b, dict) else str(b) for b in content
                )
                result.append({"role": role, "content": text})

    return result


def _openai_resp_to_anthropic(resp: Any) -> dict:
    """Convert OpenAI response object to Anthropic-format dict."""
    msg = resp.choices[0].message
    content: list[dict] = []
    if msg.content:
        content.append({"type": "text", "text": msg.content})
    if msg.tool_calls:
        for tc in msg.tool_calls:
            content.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.function.name,
                "input": json.loads(tc.function.arguments or "{}"),
            })
    return {
        "content": content,
        "stop_reason": "tool_use" if msg.tool_calls else "end_turn",
        "usage": {
            "input_tokens": resp.usage.prompt_tokens,
            "output_tokens": resp.usage.completion_tokens,
        },
    }


# ── Public API ────────────────────────────────────────────────────────────────

def chat(*, node: str, system: str, messages: list[dict], tools: list[dict] | None = None,
         max_tokens: int = 2048) -> dict[str, Any]:
    """Call the LLM with exponential-backoff retry. Returns Anthropic-format dict."""
    t0 = time.time()
    last_exc: Exception | None = None

    with _tracer.start_as_current_span(f"llm.{node}") as span:
        span.set_attribute("llm.node", node)
        span.set_attribute("llm.model", MODEL)
        span.set_attribute("llm.backend", _BACKEND)

        for attempt in range(_MAX_RETRIES):
            try:
                if _BACKEND == "groq":
                    oai_messages = [{"role": "system", "content": system}] + _messages_to_openai(messages)
                    kwargs: dict[str, Any] = {
                        "model": MODEL,
                        "max_tokens": max_tokens,
                        "messages": oai_messages,
                    }
                    if tools:
                        kwargs["tools"] = _tools_to_openai(tools)
                        kwargs["tool_choice"] = "auto"
                    raw = _client.chat.completions.create(**kwargs)
                    resp = _openai_resp_to_anthropic(raw)
                else:
                    raw = _client.messages.create(
                        model=MODEL,
                        max_tokens=max_tokens,
                        system=system,
                        messages=messages,
                        tools=tools or [],
                    )
                    resp = raw.model_dump()

                elapsed = time.time() - t0
                in_tok = resp["usage"]["input_tokens"]
                out_tok = resp["usage"]["output_tokens"]
                LLM_LATENCY.labels(node, MODEL).observe(elapsed)
                LLM_CALLS.labels(node, MODEL, "ok").inc()
                LLM_TOKENS.labels(node, MODEL, "input").inc(in_tok)
                LLM_TOKENS.labels(node, MODEL, "output").inc(out_tok)
                span.set_attribute("llm.input_tokens", in_tok)
                span.set_attribute("llm.output_tokens", out_tok)
                span.set_attribute("llm.result", "ok")
                return resp

            except Exception as exc:
                last_exc = exc
                span.set_attribute("llm.result", "error")
                span.record_exception(exc)
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_BASE_S * (2 ** attempt))

    LLM_CALLS.labels(node, MODEL, "error").inc()
    raise last_exc


def extract_text(resp: dict) -> str:
    """Concatenate text blocks from a Messages-API response."""
    return "".join(b.get("text", "") for b in resp["content"] if b.get("type") == "text")


def extract_tool_uses(resp: dict) -> list[dict]:
    return [b for b in resp["content"] if b.get("type") == "tool_use"]


def parse_json_block(text: str) -> dict:
    """LLM output between ```json fences → dict. Falls back to first {...}."""
    import re
    m = re.search(r"```json\s*(.+?)\s*```", text, re.S)
    raw = m.group(1) if m else None
    if raw is None:
        start = text.find("{")
        if start < 0:
            raise ValueError("no JSON in response")
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    raw = text[start : i + 1]
                    break
    if raw is None:
        raise ValueError("unbalanced JSON")
    return json.loads(raw)
