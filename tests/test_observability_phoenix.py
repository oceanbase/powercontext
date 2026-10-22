"""Phoenix is declared once and stays wired to the Collector and the saved port."""

import pytest
import yaml

from powercontext.observability.deployment import ObservabilityError, initialize, load_state


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    monkeypatch.setattr("powercontext.observability.deployment._check_port", lambda port: None)
    return tmp_path, initialize(
        tmp_path,
        server_url="http://127.0.0.1:8000",
        token="existing-token",  # noqa: S106 - isolated fixture credential.
        grafana_port=13000,
        gateway_port=14318,
        phoenix_port=16006,
    )


def test_compose_and_collector_declare_phoenix(deployment):
    directory, state = deployment
    manifest = yaml.safe_load((directory / "compose.yaml").read_text())
    assert manifest["services"]["phoenix"]["ports"] == ["127.0.0.1:16006:6006"]
    assert "phoenix" in manifest["volumes"]
    config = yaml.safe_load((directory / "collector" / "config.yaml").read_text())
    assert config["exporters"]["otlphttp/phoenix"]["endpoint"] == "http://phoenix:6006"
    assert config["service"]["pipelines"]["traces"]["exporters"] == ["otlphttp/tempo", "otlphttp/phoenix"]
    assert load_state(directory)["phoenix_port"] == 16006
    assert state["phoenix_port"] == 16006


def test_default_phoenix_port_is_saved_explicitly(tmp_path, monkeypatch):
    monkeypatch.setattr("powercontext.observability.deployment._check_port", lambda port: None)
    state = initialize(
        tmp_path,
        server_url="http://127.0.0.1:8000",
        token="existing-token",  # noqa: S106 - isolated fixture credential.
        grafana_port=13000,
        gateway_port=14318,
    )
    assert state["phoenix_port"] == 6006
    assert load_state(tmp_path)["phoenix_port"] == 6006


def test_conflicting_port_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("powercontext.observability.deployment._check_port", lambda port: None)
    with pytest.raises(ObservabilityError, match="ports must differ"):
        initialize(
            tmp_path,
            server_url="http://127.0.0.1:8000",
            token="existing-token",  # noqa: S106 - isolated fixture credential.
            grafana_port=13000,
            gateway_port=14318,
            phoenix_port=13000,
        )
