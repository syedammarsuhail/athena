"""
Datadog APM integration for Athena agent.

Activated when DD_API_KEY is set in environment.
Works alongside OTel — both can run at the same time.

How companies use Datadog APM:
  - Airbnb: traces every booking request across 200+ services
  - Slack: traces every message delivery, finds slow DB queries
  - Samsung: monitors IoT device telemetry pipelines

Usage: wrap any function with @trace_node("node_name")
"""
from __future__ import annotations

import os
import functools
import logging
from typing import Callable

log = logging.getLogger(__name__)

DD_ENABLED = bool(os.getenv("DD_API_KEY"))


def trace_node(node_name: str):
    """
    Decorator that creates a Datadog span for a graph node.
    No-ops cleanly if DD_API_KEY is not set.
    """
    def decorator(func: Callable) -> Callable:
        if not DD_ENABLED:
            return func

        try:
            from ddtrace import tracer

            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                with tracer.trace(
                    f"athena.{node_name}",
                    service="athena-agent",
                    resource=node_name,
                    span_type="custom",
                ) as span:
                    state = args[0] if args else {}
                    event = state.get("event")
                    if event:
                        span.set_tag("incident.service",   getattr(event, "service", ""))
                        span.set_tag("incident.namespace", getattr(event, "namespace", ""))
                        span.set_tag("incident.severity",  str(state.get("severity", "")))
                    result = func(*args, **kwargs)
                    span.set_tag("incident.resolved", str(result.get("resolved", "")))
                    return result
            return wrapper
        except ImportError:
            log.warning("ddtrace not installed; Datadog APM disabled")
            return func

    return decorator


def init_datadog():
    """Call once at startup to configure Datadog APM."""
    if not DD_ENABLED:
        return
    try:
        from ddtrace import patch_all, config
        patch_all(logging=True)   # auto-instruments httpx, redis, etc.
        config.service = "athena-agent"
        config.env     = os.getenv("DD_ENV", "dev")
        config.version = os.getenv("DD_VERSION", "1.0.0")
        log.info("Datadog APM initialized → traces will appear in Datadog APM")
    except ImportError:
        log.warning("ddtrace not installed")
