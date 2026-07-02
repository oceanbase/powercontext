import json

import pytest


def test_public_health_does_not_expose_startup_error():
    pytest.importorskip("fastapi", exc_type=ImportError)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.api.v1.system import router

    app = FastAPI()
    app.state.service_ready = False
    app.state.storage_type = "sqlite"
    app.state.service_startup_error = (
        "failed to connect to postgresql://user:secret@db.internal/powermem "
        "from C:/sensitive/path"
    )
    app.include_router(router, prefix="/api/v1")

    response = TestClient(app).get("/api/v1/system/health")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["status"] == "degraded"
    assert body["data"]["memory_service_ready"] is False
    assert "startup_error" not in body["data"]

    response_text = json.dumps(body)
    assert "secret" not in response_text
    assert "db.internal" not in response_text
    assert "sensitive/path" not in response_text
