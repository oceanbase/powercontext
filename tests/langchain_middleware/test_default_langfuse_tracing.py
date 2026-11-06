# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Exercise the published example with the official adapter and local ASGI runtime."""

import asyncio
import importlib.util
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

pytest.importorskip("langfuse")
from langchain.agents import create_agent
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from powercontext_langchain import PowerContextMiddleware, PowerContextScope


class LocalModel(BaseChatModel):
    @property
    def _llm_type(self):
        return "local-fixture"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Check the deployment plan."))])


class DefaultLangfuseTracingTests(unittest.TestCase):
    def test_plain_middleware_exports_complete_tree_with_default_langfuse(self):
        if importlib.util.find_spec("powercontext_langchain") is None:
            self.skipTest("Install the matching PowerContext core and LangChain adapter")
        # An installed but incompatible/broken adapter must fail rather than skip.
        from powercontext_langchain.client import shared_http_client

        from powercontext.builtin.persistence.sqlite import SQLiteConfig
        from powercontext.builtin.runtime import InferenceConfig, RuntimeConfig
        from powercontext.server.factory import create_server_app
        from powercontext.server.settings import McpConfig, ServerSettings
        from powercontext.server.tracing import ServerTracing

        exporter = InMemorySpanExporter()
        provider = TracerProvider(shutdown_on_exit=False)
        server_provider = TracerProvider(shutdown_on_exit=False)
        server_provider.add_span_processor(SimpleSpanProcessor(exporter))
        key = "powercontext-example-" + uuid4().hex
        real_langfuse = Langfuse
        clients = []

        def initialize(**kwargs):
            client = real_langfuse(tracer_provider=provider, **kwargs)
            clients.append(client)
            return client

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(
                os.environ,
                {
                    "LANGFUSE_PUBLIC_KEY": key,
                    "LANGFUSE_SECRET_KEY": "local-test-only",
                    "LANGFUSE_BASE_URL": "http://127.0.0.1:9",
                    "LANGFUSE_MEDIA_UPLOAD_ENABLED": "false",
                    "OPENAI_API_KEY": "local-test-only",
                    "OPENAI_MODEL": "local-fixture",
                    "OPENAI_BASE_URL": "http://127.0.0.1:9",
                    "POWERCONTEXT_LANGCHAIN_BASE_URL": "http://testserver",
                    "POWERCONTEXT_LANGCHAIN_ALLOW_INSECURE_HTTP": "true",
                    "LANGSMITH_TRACING": "false",
                },
            ),
            patch("requests.sessions.Session.send", side_effect=AssertionError("network forbidden")),
            patch.object(OTLPSpanExporter, "export", side_effect=exporter.export),
            patch.object(trace, "get_tracer", side_effect=provider.get_tracer),
            patch("langfuse.Langfuse", side_effect=initialize),
            patch("langchain_openai.ChatOpenAI", side_effect=lambda **kwargs: LocalModel()),
        ):
            app = create_server_app(
                settings=ServerSettings(
                    database=SQLiteConfig(url=f"sqlite+aiosqlite:///{directory}/runtime.db"),
                    inference=InferenceConfig(generation_model="test"),
                    runtime=RuntimeConfig(artifact_processing_families=()),
                    mcp=McpConfig(enabled=False),
                ),
                tracing=ServerTracing(server_provider),
            )

            async def invoke():
                async with (
                    app.router.lifespan_context(app),
                    httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http,
                ):
                    with shared_http_client(http, trust_transport_security=True), redirect_stdout(io.StringIO()):
                        client = initialize()
                        agent = create_agent(
                            model=LocalModel(),
                            tools=[],
                            middleware=[PowerContextMiddleware(auto_capture=True)],
                            context_schema=PowerContextScope,
                        )
                        with client.start_as_current_observation(
                            name="langchain-powercontext-request", as_type="agent"
                        ):
                            await agent.ainvoke(
                                {"messages": [{"role": "user", "content": "PRIVATE_QUERY_SENTINEL"}]},
                                config={"callbacks": [CallbackHandler(public_key=key)]},
                                context=PowerContextScope(),
                            )
                        client.flush()

            try:
                asyncio.run(invoke())
                spans = exporter.get_finished_spans()
                root = next(span for span in spans if span.name == "langchain-powercontext-request")
                request_spans = [span for span in spans if span.context.trace_id == root.context.trace_id]
                by_id = {span.context.span_id: span for span in request_spans}
                self.assertIsNone(root.parent)
                self.assertEqual([s.name for s in request_spans if s.parent is None], [root.name])
                for span in request_spans:
                    if span.parent:
                        self.assertIn(span.parent.span_id, by_id, span.name)
                by_name = {span.name: span for span in request_spans}
                client = by_name["PowerContextClient prepare_context"]
                self.assertEqual(by_name["HTTP prepare_context"].parent.span_id, client.context.span_id)
                self.assertIn("memory.search", by_name)
                self.assertIn("PowerContextClient capture_content_source", by_name)
                self.assertIn("LocalModel", by_name)
                self.assertEqual(client.attributes["gen_ai.operation.name"], "retrieval")
                self.assertEqual(client.attributes["gen_ai.provider.name"], "powercontext")
                for span in request_spans:
                    if span.instrumentation_scope.name == "powercontext.client":
                        self.assertNotIn("PRIVATE_QUERY_SENTINEL", str(span.attributes))
                from powercontext.client.tracing import ClientSpan

                standalone = ClientSpan.start("get_server_info")
                standalone.finish("success")
                ordinary_span = provider.get_tracer("ordinary.http").start_span("not-a-genai-span")
                ordinary_span.end()
                provider.force_flush()
                self.assertNotIn("not-a-genai-span", [s.name for s in exporter.get_finished_spans()])
                self.assertNotIn("PowerContextClient get_server_info", [s.name for s in exporter.get_finished_spans()])
            finally:
                for client in clients:
                    client.shutdown()
                provider.shutdown()
                server_provider.shutdown()


if __name__ == "__main__":
    unittest.main()
