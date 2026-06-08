import builtins
import importlib
import sys

import pytest

from powermem.integrations.embeddings.config.base import BaseEmbedderConfig


def _restore_module(module_name, saved_module):
    if saved_module is None:
        sys.modules.pop(module_name, None)
    else:
        sys.modules[module_name] = saved_module


def test_mcp_missing_fastmcp_raises_import_error_not_system_exit(monkeypatch):
    module_name = "powermem.mcp.server"
    saved_module = sys.modules.pop(module_name, None)
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "fastmcp" or name.startswith("fastmcp."):
            raise ImportError("fastmcp unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    try:
        with pytest.raises(ImportError, match="powermem\\[mcp\\]"):
            importlib.import_module(module_name)
    finally:
        _restore_module(module_name, saved_module)


def test_ollama_embedding_missing_dependency_imports_without_prompt(monkeypatch):
    module_name = "powermem.integrations.embeddings.ollama"
    saved_module = sys.modules.pop(module_name, None)
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "ollama" or name.startswith("ollama."):
            raise ImportError("ollama unavailable")
        return real_import(name, *args, **kwargs)

    def fail_input(*args, **kwargs):
        raise AssertionError("missing optional dependency should not prompt on import")

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(builtins, "input", fail_input)

    try:
        module = importlib.import_module(module_name)
        config = BaseEmbedderConfig(model="nomic-embed-text", embedding_dims=512)
        with pytest.raises(ImportError, match="ollama"):
            module.OllamaEmbedding(config)
    finally:
        _restore_module(module_name, saved_module)
