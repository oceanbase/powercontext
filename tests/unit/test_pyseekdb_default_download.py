"""Unit tests for the CN-aware model download logic in pyseekdb_default.

Covers:
- _detect_country: IP API success / partial failure / all failure / dirty data
- _download_via_modelscope: uv lookup, PyPI index selection, subprocess args
- _bridge_modelscope_to_hf_cache: directory layout, idempotency, fallback SHA
- _load_sentence_transformer_with_fallback: cache hit, CN path, non-CN path,
  detection failure fallback, error propagation, sentence_transformers absent
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, call, patch

import pytest

from powermem.integrations.embeddings import _model_cache, pyseekdb_default


# ---------------------------------------------------------------------------
# _detect_country
# ---------------------------------------------------------------------------


class TestDetectCountry:
    def _mock_urlopen(self, responses):
        """responses: list of (bytes | Exception) per URL call."""
        calls = iter(responses)

        def side_effect(url, timeout=3):
            resp = next(calls)
            if isinstance(resp, Exception):
                raise resp
            mock_resp = MagicMock()
            mock_resp.read.return_value = resp
            return mock_resp

        return side_effect

    def test_first_api_returns_cn(self):
        side_effect = self._mock_urlopen([b"CN"])
        with patch.object(_model_cache.urllib.request, "urlopen", side_effect=side_effect):
            assert pyseekdb_default._detect_country() == "CN"

    def test_first_fails_second_returns_us(self):
        side_effect = self._mock_urlopen([OSError("timeout"), b"US"])
        with patch.object(_model_cache.urllib.request, "urlopen", side_effect=side_effect):
            assert pyseekdb_default._detect_country() == "US"

    def test_all_fail_returns_empty(self):
        side_effect = self._mock_urlopen([OSError(), OSError(), OSError()])
        with patch.object(_model_cache.urllib.request, "urlopen", side_effect=side_effect):
            assert pyseekdb_default._detect_country() == ""

    def test_dirty_data_is_skipped(self):
        # First returns non-alpha data (digits), second returns valid
        side_effect = self._mock_urlopen([b"123", b"CN"])
        with patch.object(_model_cache.urllib.request, "urlopen", side_effect=side_effect):
            assert pyseekdb_default._detect_country() == "CN"

    def test_lowercase_normalised_to_upper(self):
        side_effect = self._mock_urlopen([b"cn"])
        with patch.object(_model_cache.urllib.request, "urlopen", side_effect=side_effect):
            assert pyseekdb_default._detect_country() == "CN"


# ---------------------------------------------------------------------------
# _download_via_modelscope
# ---------------------------------------------------------------------------


class TestDownloadViaModelscope:
    def test_raises_when_uv_not_found(self, monkeypatch):
        monkeypatch.delenv("POWERMEM_UV_BIN", raising=False)
        with patch.object(_model_cache.shutil, "which", return_value=None), \
             patch.object(_model_cache.os.path, "isfile", return_value=False):
            with pytest.raises(RuntimeError, match="uv is required"):
                pyseekdb_default._download_via_modelscope()

    def test_uses_powermem_uv_index_url_when_set(self, monkeypatch, tmp_path):
        fake_uv = str(tmp_path / "uv")
        Path(fake_uv).touch()
        monkeypatch.setenv("POWERMEM_UV_BIN", fake_uv)
        monkeypatch.setenv("POWERMEM_UV_INDEX_URL", "https://custom.index/simple")

        captured = []

        def fake_run(cmd, check):
            captured.append(cmd)

        with patch.object(_model_cache.subprocess, "run", side_effect=fake_run), \
             patch.object(_model_cache.os.path, "isfile", return_value=True):
            pyseekdb_default._download_via_modelscope(country="CN")

        assert "--default-index" in captured[0]
        assert "https://custom.index/simple" in captured[0]

    def test_cn_without_index_url_uses_tsinghua(self, monkeypatch, tmp_path):
        fake_uv = str(tmp_path / "uv")
        Path(fake_uv).touch()
        monkeypatch.setenv("POWERMEM_UV_BIN", fake_uv)
        monkeypatch.delenv("POWERMEM_UV_INDEX_URL", raising=False)

        captured = []

        def fake_run(cmd, check):
            captured.append(cmd)

        with patch.object(_model_cache.subprocess, "run", side_effect=fake_run), \
             patch.object(_model_cache.os.path, "isfile", return_value=True):
            pyseekdb_default._download_via_modelscope(country="CN")

        assert "https://pypi.tuna.tsinghua.edu.cn/simple" in captured[0]

    def test_non_cn_without_index_url_no_default_index(self, monkeypatch, tmp_path):
        fake_uv = str(tmp_path / "uv")
        Path(fake_uv).touch()
        monkeypatch.setenv("POWERMEM_UV_BIN", fake_uv)
        monkeypatch.delenv("POWERMEM_UV_INDEX_URL", raising=False)

        captured = []

        def fake_run(cmd, check):
            captured.append(cmd)

        with patch.object(_model_cache.subprocess, "run", side_effect=fake_run), \
             patch.object(_model_cache.os.path, "isfile", return_value=True):
            pyseekdb_default._download_via_modelscope(country="US")

        assert "--default-index" not in captured[0]

    def test_bootstrap_python_used_in_cmd(self, monkeypatch, tmp_path):
        fake_uv = str(tmp_path / "uv")
        Path(fake_uv).touch()
        monkeypatch.setenv("POWERMEM_UV_BIN", fake_uv)
        monkeypatch.setenv("POWERMEM_BOOTSTRAP_PYTHON", "/usr/bin/python3.11")
        monkeypatch.delenv("POWERMEM_UV_INDEX_URL", raising=False)

        captured = []

        def fake_run(cmd, check):
            captured.append(cmd)

        with patch.object(_model_cache.subprocess, "run", side_effect=fake_run), \
             patch.object(_model_cache.os.path, "isfile", return_value=True):
            pyseekdb_default._download_via_modelscope()

        assert "--python" in captured[0]
        idx = captured[0].index("--python")
        assert captured[0][idx + 1] == "/usr/bin/python3.11"

    def test_subprocess_failure_propagates(self, monkeypatch, tmp_path):
        import subprocess as sp

        fake_uv = str(tmp_path / "uv")
        Path(fake_uv).touch()
        monkeypatch.setenv("POWERMEM_UV_BIN", fake_uv)
        monkeypatch.delenv("POWERMEM_UV_INDEX_URL", raising=False)

        with patch.object(_model_cache.subprocess, "run",
                          side_effect=sp.CalledProcessError(1, "uv")), \
             patch.object(_model_cache.os.path, "isfile", return_value=True):
            with pytest.raises(sp.CalledProcessError):
                pyseekdb_default._download_via_modelscope()

    def test_custom_modelscope_package(self, monkeypatch, tmp_path):
        fake_uv = str(tmp_path / "uv")
        Path(fake_uv).touch()
        monkeypatch.setenv("POWERMEM_UV_BIN", fake_uv)
        monkeypatch.setenv("POWERMEM_MODELSCOPE_PACKAGE", "modelscope==1.99.0")
        monkeypatch.delenv("POWERMEM_UV_INDEX_URL", raising=False)

        captured = []

        def fake_run(cmd, check):
            captured.append(cmd)

        with patch.object(_model_cache.subprocess, "run", side_effect=fake_run), \
             patch.object(_model_cache.os.path, "isfile", return_value=True):
            pyseekdb_default._download_via_modelscope()

        assert "modelscope==1.99.0" in captured[0]


# ---------------------------------------------------------------------------
# _bridge_modelscope_to_hf_cache
# ---------------------------------------------------------------------------


class TestBridgeModelscope:
    def _setup_modelscope_src(self, tmp_path):
        """Create a fake ModelScope cache directory with model files."""
        org, name = pyseekdb_default._MODELSCOPE_REPO_ID.split("/", 1)
        src = tmp_path / ".cache" / "modelscope" / "hub" / "models" / org / name
        src.mkdir(parents=True)
        (src / "tokenizer.json").write_text("fake tokenizer")
        (src / "config.json").write_text("{}")
        (src / "configuration.json").write_text("skip me")  # should be skipped
        (src / "data_config.json").write_text("skip me")    # should be skipped
        sub = src / "subdir"
        sub.mkdir()
        (sub / "weights.bin").write_bytes(b"\x00\x01")
        return src

    def test_creates_hf_cache_layout(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        self._setup_modelscope_src(tmp_path)

        fake_sha = "abc123" + "0" * 34

        def fake_urlopen(url, timeout=5):
            resp = MagicMock()
            resp.read.return_value = json.dumps({"sha": fake_sha}).encode()
            return resp

        with patch.object(_model_cache.urllib.request, "urlopen", side_effect=fake_urlopen):
            pyseekdb_default._bridge_modelscope_to_hf_cache()

        hf_dir_name = "models--" + pyseekdb_default.DEFAULT_MODEL_REPO_ID.replace("/", "--")
        hub = tmp_path / ".cache" / "huggingface" / "hub" / hf_dir_name
        snap = hub / "snapshots" / fake_sha

        assert snap.exists()
        assert (hub / "refs" / "main").read_text() == fake_sha
        assert (snap / "tokenizer.json").exists()
        assert (snap / "subdir" / "weights.bin").exists()
        # skipped files must not appear
        assert not (snap / "configuration.json").exists()
        assert not (snap / "data_config.json").exists()

    def test_existing_files_not_overwritten(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        src = self._setup_modelscope_src(tmp_path)

        fake_sha = "def456" + "0" * 34
        hf_dir_name = "models--" + pyseekdb_default.DEFAULT_MODEL_REPO_ID.replace("/", "--")
        snap = tmp_path / ".cache" / "huggingface" / "hub" / hf_dir_name / "snapshots" / fake_sha
        snap.mkdir(parents=True)
        existing = snap / "tokenizer.json"
        existing.write_text("original")

        def fake_urlopen(url, timeout=5):
            resp = MagicMock()
            resp.read.return_value = json.dumps({"sha": fake_sha}).encode()
            return resp

        with patch.object(_model_cache.urllib.request, "urlopen", side_effect=fake_urlopen):
            pyseekdb_default._bridge_modelscope_to_hf_cache()

        assert existing.read_text() == "original"

    def test_hf_api_failure_uses_fallback_sha(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        self._setup_modelscope_src(tmp_path)

        with patch.object(_model_cache.urllib.request, "urlopen",
                          side_effect=OSError("no network")):
            pyseekdb_default._bridge_modelscope_to_hf_cache()

        hf_dir_name = "models--" + pyseekdb_default.DEFAULT_MODEL_REPO_ID.replace("/", "--")
        hub = tmp_path / ".cache" / "huggingface" / "hub" / hf_dir_name
        assert (hub / "snapshots" / pyseekdb_default._HF_REVISION_FALLBACK).exists()

    def test_missing_modelscope_src_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        with pytest.raises(RuntimeError, match="ModelScope cache not found"):
            pyseekdb_default._bridge_modelscope_to_hf_cache()


# ---------------------------------------------------------------------------
# _load_sentence_transformer_with_fallback
# ---------------------------------------------------------------------------


class TestLoadSentenceTransformerWithFallback:
    MODEL = pyseekdb_default.DEFAULT_MODEL_NAME
    REPO = pyseekdb_default.DEFAULT_MODEL_REPO_ID

    def test_cache_hit_loads_locally_no_download(self):
        mock_st_cls = MagicMock(return_value=MagicMock())
        with patch.object(_model_cache, "is_model_cached", return_value=True), \
             patch.object(_model_cache, "detect_country") as mock_detect, \
             patch.object(pyseekdb_default, "_patch_sentence_transformer_cache"), \
             patch.dict("sys.modules", {"sentence_transformers": MagicMock(
                 SentenceTransformer=mock_st_cls)}):
            pyseekdb_default._load_sentence_transformer_with_fallback(self.MODEL, self.REPO)

        mock_detect.assert_not_called()

    def test_cache_miss_cn_triggers_modelscope(self):
        with patch.object(_model_cache, "is_model_cached", return_value=False), \
             patch.object(_model_cache, "detect_country", return_value="CN"), \
             patch.object(_model_cache, "download_via_modelscope") as mock_dl, \
             patch.object(_model_cache, "bridge_modelscope_to_hf_cache") as mock_bridge, \
             patch.dict("sys.modules", {"sentence_transformers": None}):
            pyseekdb_default._load_sentence_transformer_with_fallback(self.MODEL, self.REPO)

        mock_dl.assert_called_once_with(country="CN")
        mock_bridge.assert_called_once()

    def test_cache_miss_non_cn_uses_hf_path(self):
        mock_st_instance = MagicMock()
        mock_st_cls = MagicMock(return_value=mock_st_instance)

        with patch.object(_model_cache, "is_model_cached", return_value=False), \
             patch.object(_model_cache, "detect_country", return_value="US"), \
             patch.object(_model_cache, "download_via_modelscope") as mock_dl, \
             patch.dict("sys.modules", {"sentence_transformers": MagicMock(
                 SentenceTransformer=mock_st_cls)}):
            pyseekdb_default._load_sentence_transformer_with_fallback(self.MODEL, self.REPO)

        mock_dl.assert_not_called()

    def test_cache_miss_detection_failed_uses_hf_path(self):
        mock_st_instance = MagicMock()
        mock_st_cls = MagicMock(return_value=mock_st_instance)

        with patch.object(_model_cache, "is_model_cached", return_value=False), \
             patch.object(_model_cache, "detect_country", return_value=""), \
             patch.object(_model_cache, "download_via_modelscope") as mock_dl, \
             patch.dict("sys.modules", {"sentence_transformers": MagicMock(
                 SentenceTransformer=mock_st_cls)}):
            pyseekdb_default._load_sentence_transformer_with_fallback(self.MODEL, self.REPO)

        mock_dl.assert_not_called()

    def test_cn_modelscope_failure_raises_runtime_error(self):
        with patch.object(_model_cache, "is_model_cached", return_value=False), \
             patch.object(_model_cache, "detect_country", return_value="CN"), \
             patch.object(_model_cache, "download_via_modelscope",
                          side_effect=RuntimeError("uvx failed")):
            with pytest.raises(RuntimeError, match="uvx failed"):
                pyseekdb_default._load_sentence_transformer_with_fallback(self.MODEL, self.REPO)

    def test_non_cn_hf_timeout_raises_runtime_error(self):
        import threading as _threading

        # Make the thread never set result[0] (simulates timeout)
        original_join = _threading.Thread.join

        def slow_join(self_thread, timeout=None):
            if timeout is not None:
                return  # return immediately, result[0] stays None
            return original_join(self_thread, timeout)

        with patch.object(_model_cache, "is_model_cached", return_value=False), \
             patch.object(_model_cache, "detect_country", return_value="US"), \
             patch.dict("sys.modules", {"sentence_transformers": MagicMock(
                 SentenceTransformer=MagicMock(side_effect=lambda _: None))}), \
             patch.object(_threading.Thread, "join", slow_join):
            with pytest.raises(RuntimeError, match="timed out"):
                pyseekdb_default._load_sentence_transformer_with_fallback(self.MODEL, self.REPO)

    def test_sentence_transformers_absent_cn_still_downloads(self):
        """CN download must run even when sentence_transformers is not installed."""
        with patch.object(_model_cache, "is_model_cached", return_value=False), \
             patch.object(_model_cache, "detect_country", return_value="CN"), \
             patch.object(_model_cache, "download_via_modelscope") as mock_dl, \
             patch.object(_model_cache, "bridge_modelscope_to_hf_cache"), \
             patch.dict("sys.modules", {"sentence_transformers": None}):
            result = pyseekdb_default._load_sentence_transformer_with_fallback(
                self.MODEL, self.REPO
            )

        mock_dl.assert_called_once()
        assert result is None  # no ST, but download happened

    def test_sentence_transformers_absent_non_cn_returns_none(self):
        with patch.object(_model_cache, "is_model_cached", return_value=False), \
             patch.object(_model_cache, "detect_country", return_value="US"), \
             patch.dict("sys.modules", {"sentence_transformers": None}):
            result = pyseekdb_default._load_sentence_transformer_with_fallback(
                self.MODEL, self.REPO
            )

        assert result is None
