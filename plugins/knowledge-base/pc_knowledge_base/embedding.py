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

"""An independently configured local model, with explicit input/dimension checks."""

from __future__ import annotations

import asyncio
import importlib.metadata
import threading
from pathlib import Path

from pc_knowledge_base.content import ContentError
from pc_knowledge_base.models import identity


class Embedding:
    def __init__(self, model, cache):
        from fastembed import TextEmbedding

        matches = [m for m in TextEmbedding.list_supported_models() if m["model"] == model]
        if len(matches) != 1:
            raise ValueError("Embedding model must be supported by FastEmbed")  # noqa: TRY003
        if model != "BAAI/bge-small-zh-v1.5":
            raise ValueError("This release supports the pinned BAAI/bge-small-zh-v1.5 model")  # noqa: TRY003
        self.revision = "46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59"
        self.model_name = model
        self.dimension = matches[0]["dim"]
        self.cache = cache
        self.profile = identity(
            "fastembed-v1",
            model,
            self.dimension,
            self.revision,
            importlib.metadata.version("fastembed"),
            "passage_embed/query_embed;reject-overlong",
        )
        self._model = None
        self._lock = threading.Lock()

    def _embed(self, values, query):
        from fastembed import TextEmbedding

        with self._lock:
            if self._model is None:
                from huggingface_hub import snapshot_download

                cached = Path(self.cache) / "models--Qdrant--bge-small-zh-v1.5" / "snapshots" / self.revision
                files = [
                    "config.json",
                    "tokenizer.json",
                    "tokenizer_config.json",
                    "special_tokens_map.json",
                    "model_optimized.onnx",
                ]
                model_path = (
                    str(cached)
                    if all((cached / name).is_file() for name in files)
                    else snapshot_download(
                        "Qdrant/bge-small-zh-v1.5", revision=self.revision, cache_dir=self.cache, allow_patterns=files
                    )
                )
                self._model = TextEmbedding(model_name=self.model_name, specific_model_path=model_path, threads=2)
            # This pinned model uses the same unprefixed input for queries and passages. Validate
            # with a separate, non-truncating tokenizer before invoking inference.
            import tokenizers
            from fastembed.text.onnx_embedding import OnnxTextEmbedding

            model = self._model.model
            if not isinstance(model, OnnxTextEmbedding) or model.tokenizer is None:
                raise RuntimeError("Expected a loaded ONNX tokenizer")  # noqa: TRY003
            tokenizer = tokenizers.Tokenizer.from_str(model.tokenizer.to_str())
            tokenizer.no_truncation()
            maximum = model.tokenizer.truncation
            maximum = 512 if maximum is None else maximum["max_length"]
            if any(len(tokenizer.encode(value).ids) > maximum for value in values):
                raise ContentError("Embedding input exceeds the model token limit; reduce chunk size or query length")  # noqa: TRY003
            method = self._model.query_embed if query else self._model.passage_embed
            vectors = [vector.tolist() for vector in method(values)]
            if any(len(v) != self.dimension for v in vectors):
                raise RuntimeError("Embedding dimension does not match the pinned profile")  # noqa: TRY003
            return vectors

    async def embed(self, values, query=False):
        if not values:
            return []
        return await asyncio.to_thread(self._embed, values, query)
