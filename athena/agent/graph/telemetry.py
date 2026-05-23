"""
OpenTelemetry setup for the Athena agent.

Every graph node wraps its work in an OTel span. This lets you see in Tempo:
  detect (2.3s) → diagnose (12.1s) → plan (1.2s) → execute (0.4s) → verify (31s)

How companies use this:
  - Uber: traces every incident response to measure MTTR per team
  - Shopify: correlates trace_id with Slack incident thread
  - Atlassian: SLA reporting on how fast automation resolves incidents
"""
from __future__ import annotations

import os
import logging

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

log = logging.getLogger(__name__)

OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
SERVICE_NAME  = os.getenv("OTEL_SERVICE_NAME", "athena-agent")


def setup_tracing() -> trace.Tracer:
    resource = Resource.create({"service.name": SERVICE_NAME})
    provider = TracerProvider(resource=resource)

    if OTEL_ENDPOINT:
        # Production: send to OTel Collector → Tempo
        exporter = OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True)
        log.info("OTel traces → %s", OTEL_ENDPOINT)
    else:
        # Dev fallback: print spans to stdout
        exporter = ConsoleSpanExporter()
        log.info("OTel traces → stdout (set OTEL_EXPORTER_OTLP_ENDPOINT to use Tempo)")

    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return trace.get_tracer(SERVICE_NAME)


# Global tracer — imported by all graph nodes
tracer = setup_tracing()


def get_tracer() -> trace.Tracer:
    return tracer
