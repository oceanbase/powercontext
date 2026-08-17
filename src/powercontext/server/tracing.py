"""Standard OpenTelemetry tracing for the ready-to-run Server."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from importlib import import_module
from typing import TYPE_CHECKING, Any

from fastmcp.server.dependencies import get_http_headers, get_http_request
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from opentelemetry import context as otel_context
from opentelemetry import propagate, trace
from opentelemetry.context import Context, Token
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.id_generator import RandomIdGenerator
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ParentBased
from opentelemetry.trace import Span, SpanKind, Status, StatusCode, Tracer, set_span_in_context
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from powercontext.server.context import bind_request_id, is_internal_bridge, reset_request_id
from powercontext.server.settings import TracingConfig

if TYPE_CHECKING:
    from pydantic_ai.models.instrumented import InstrumentationSettings

_INSTRUMENTATION_NAME = "powercontext.server"
_ID_GENERATOR = RandomIdGenerator()
_MISSING_OTLP_EXPORTER = (
    "OpenTelemetry export requires the 'tracing-otlp' extra; "
    "install 'powercontext[server,tracing-otlp]' before enabling tracing"
)


class ServerTracing:
    """Create failure-isolated spans with one configured tracer provider."""

    def __init__(self, provider: TracerProvider, *, instrumented: bool = False) -> None:
        self.provider = provider
        self.tracer = provider.get_tracer(_INSTRUMENTATION_NAME)
        self.instrumentation = _inference_instrumentation(provider) if instrumented else None

    @classmethod
    def context_only(cls) -> ServerTracing:
        """Create valid request contexts without recording or exporting spans."""

        return cls(
            TracerProvider(
                resource=_server_resource(),
                sampler=ParentBased(ALWAYS_OFF),
                shutdown_on_exit=False,
            )
        )

    def start_span(
        self,
        name: str,
        *,
        kind: SpanKind,
        attributes: dict[str, Any],
        context: Context | None = None,
    ) -> _ActiveSpan:
        return _ActiveSpan.start(
            self.tracer,
            name,
            kind=kind,
            attributes=attributes,
            context=context,
        )

    def shutdown(self) -> None:
        with suppress(Exception):
            self.provider.shutdown()


class _ActiveSpan:
    def __init__(self, span: Span | None, token: Token[Context] | None) -> None:
        self.span = span
        self.token = token
        self.finished = False

    @classmethod
    def start(
        cls,
        tracer: Tracer,
        name: str,
        *,
        kind: SpanKind,
        attributes: dict[str, Any],
        context: Context | None,
    ) -> _ActiveSpan:
        span: Span | None = None
        try:
            span = tracer.start_span(name, context=context, kind=kind, attributes=attributes)
            token = otel_context.attach(set_span_in_context(span, context))
        except Exception:
            if span is not None:
                with suppress(Exception):
                    span.end()
            return cls(None, None)
        return cls(span, token)

    @property
    def request_id(self) -> str:
        return request_id_from_span(self.span)

    def set_attributes(self, attributes: dict[str, Any]) -> None:
        if self.span is not None:
            with suppress(Exception):
                self.span.set_attributes(attributes)

    def finish(
        self,
        outcome: str,
        *,
        error: BaseException | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        if self.finished:
            return
        self.finished = True
        if self.span is not None:
            with suppress(Exception):
                self.span.set_attribute("powercontext.operation.outcome", outcome)
                if attributes is not None:
                    self.span.set_attributes(attributes)
                if error is not None:
                    self.span.set_attribute("error.type", type(error).__qualname__)
                if outcome == "failure":
                    self.span.set_status(Status(StatusCode.ERROR))
        if self.token is not None:
            with suppress(Exception):
                otel_context.detach(self.token)
        if self.span is not None:
            with suppress(Exception):
                self.span.end()


class HttpTracingMiddleware:
    """Trace external HTTP requests while excluding infrastructure and the MCP bridge."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        tracing: ServerTracing,
        operations: dict[tuple[str, str], str],
        skip_paths: tuple[str, ...] = (),
    ) -> None:
        self.app = app
        self.tracing = tracing
        self.operations = operations
        self.skip_paths = skip_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or is_internal_bridge() or scope["path"].startswith(self.skip_paths):
            await self.app(scope, receive, send)
            return

        operation = self.operations.get((scope["method"], scope["path"]), "unmatched")
        span = self.tracing.start_span(
            f"HTTP {operation}",
            kind=SpanKind.SERVER,
            context=propagate.extract(_headers(scope)),
            attributes={
                "powercontext.operation.name": operation,
                "powercontext.operation.unit": "transport",
                "powercontext.transport": "http",
                "http.request.method": scope["method"],
            },
        )
        span.set_attributes({"powercontext.request.id": span.request_id})
        status_code = 500

        async def send_with_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_with_status)
        except asyncio.CancelledError as error:
            span.finish("cancelled", error=error)
            raise
        except Exception as error:
            span.finish("failure", error=error)
            raise
        span.finish(
            "success" if status_code < 400 else "failure",
            attributes={
                "http.response.status_code": status_code,
                **_request_id_attribute(scope),
            },
        )


class McpTracingMiddleware(Middleware):
    """Trace logical MCP requests and parent projected application spans."""

    def __init__(self, tracing: ServerTracing) -> None:
        self.tracing = tracing

    async def on_request(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        operation = f"mcp.{(context.method or 'unknown').replace('/', '.')}"
        span = self.tracing.start_span(
            f"MCP {operation}",
            kind=SpanKind.SERVER,
            context=propagate.extract(get_http_headers()),
            attributes={
                "powercontext.operation.name": operation,
                "powercontext.operation.unit": "transport",
                "powercontext.transport": "mcp",
            },
        )
        request_id = span.request_id
        span.set_attributes({"powercontext.request.id": request_id})
        with suppress(RuntimeError):
            get_http_request().state.request_id = request_id
        request_id_token = bind_request_id(request_id)
        try:
            result = await call_next(context)
        except asyncio.CancelledError as error:
            span.finish("cancelled", error=error)
            raise
        except Exception as error:
            span.finish("failure", error=error)
            raise
        finally:
            reset_request_id(request_id_token)
        span.finish("success")
        return result


def configure_server_tracing(config: TracingConfig) -> ServerTracing:
    """Build request context and optional OTLP export from standard OTel configuration."""

    exporter_type = None
    if config.enabled:
        try:
            exporter_type = import_module("opentelemetry.exporter.otlp.proto.http.trace_exporter").OTLPSpanExporter
        except ImportError as error:
            raise RuntimeError(_MISSING_OTLP_EXPORTER) from error
    provider = TracerProvider(
        resource=_server_resource(),
        sampler=None if config.enabled else ParentBased(ALWAYS_OFF),
        shutdown_on_exit=False,
    )
    if exporter_type is not None:
        provider.add_span_processor(BatchSpanProcessor(exporter_type()))
        trace.set_tracer_provider(provider)
    return ServerTracing(provider, instrumented=config.enabled)


def _inference_instrumentation(provider: TracerProvider) -> InstrumentationSettings | None:
    """Bind Pydantic AI spans to the Server provider without recording any content."""

    try:
        settings_type = import_module("pydantic_ai.models.instrumented").InstrumentationSettings
        # Prompts, responses, Memory content, and vectors stay out of spans (RFC 0016);
        # model request parameters carry the full instructions, so they are excluded too.
        return settings_type(
            tracer_provider=provider,
            include_content=False,
            include_binary_content=False,
            include_model_request_parameters=False,
        )
    except Exception:
        # Tracing setup must never break the Runtime; an unavailable adapter just stays uninstrumented.
        return None


def request_id_from_span(span: Span | None = None) -> str:
    """Return the current ingress span ID or a server-owned ID with the same shape."""

    span_context = (trace.get_current_span() if span is None else span).get_span_context()
    span_id = span_context.span_id if span_context.is_valid else _ID_GENERATOR.generate_span_id()
    return format(span_id, "016x")


def _server_resource() -> Resource:
    resource = Resource.create()
    if str(resource.attributes.get(SERVICE_NAME, "")).startswith("unknown_service"):
        resource = resource.merge(Resource({SERVICE_NAME: "powercontext-server"}))
    return resource


def _headers(scope: Scope) -> dict[str, str]:
    return {name.decode("latin-1"): value.decode("latin-1") for name, value in scope["headers"]}


def _request_id_attribute(scope: Scope) -> dict[str, str]:
    state = scope.get("state")
    request_id = state.get("request_id") if isinstance(state, dict) else None
    return {"powercontext.request.id": request_id} if isinstance(request_id, str) else {}


__all__ = [
    "HttpTracingMiddleware",
    "McpTracingMiddleware",
    "ServerTracing",
    "configure_server_tracing",
    "request_id_from_span",
]
