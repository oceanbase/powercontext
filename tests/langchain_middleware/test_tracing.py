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

"""Content bounds and cleanup run without any Langfuse/exporter extras."""

import asyncio
import json
import unittest
from unittest.mock import patch

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


class AdapterTracingTests(unittest.TestCase):
    def test_content_is_bounded_and_task_local(self):
        from powercontext_langchain.tracing import recall_content_observation

        exporter = InMemorySpanExporter()
        provider = TracerProvider(shutdown_on_exit=False)
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        try:
            with patch.object(trace, "get_tracer", side_effect=provider.get_tracer):
                with recall_content_observation("private default query", enabled=False) as output:
                    output("private default output", "ready")
                self.assertEqual(len(exporter.get_finished_spans()), 0)
                with recall_content_observation("查询" * 10000, enabled=True) as output:
                    output("召回" * 10000, "ready")
                span = exporter.get_finished_spans()[0]
                assert span.attributes is not None
                for field in ("input", "output"):
                    raw_value = span.attributes[f"{field}.value"]
                    assert isinstance(raw_value, str)
                    value = json.loads(raw_value)
                    self.assertLessEqual(len(value.encode("utf-8")), 16384)
                    self.assertTrue(span.attributes[f"powercontext.content.{field}.truncated"])
                with (
                    self.assertRaises(asyncio.CancelledError),
                    recall_content_observation("cancelled", enabled=True),
                ):
                    raise asyncio.CancelledError()
                self.assertFalse(trace.get_current_span().get_span_context().is_valid)
                self.assertNotIn("output.value", exporter.get_finished_spans()[-1].attributes)
        finally:
            provider.shutdown()
