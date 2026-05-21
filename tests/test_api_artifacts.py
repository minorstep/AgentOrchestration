import asyncio

import pytest
from fastapi.testclient import TestClient

from src.api.artifacts import (
    ArtifactIngestionService,
    artifact_service,
    read_limited_body,
)
from src.api.routes import registry
from src.api.server import create_app


@pytest.fixture(autouse=True)
def reset_state():
    registry._agents.clear()
    registry._index.clear()
    artifact_service.clear()


@pytest.fixture
def client():
    return TestClient(create_app())


def auth_headers(extra=None):
    headers = {"Authorization": "Bearer test-token"}
    if extra:
        headers.update(extra)
    return headers


def test_authorized_upload_stores_artifact_metadata(client):
    agent_id = registry.register("worker", "worker.processor")

    response = client.post(
        f"/api/v2/agents/{agent_id}/artifacts/output.log",
        content=b"artifact payload",
        headers=auth_headers({"Content-Type": "text/plain"}),
    )

    assert response.status_code == 200
    artifact = response.json()["artifact"]
    assert artifact["agent_id"] == agent_id
    assert artifact["name"] == "output.log"
    assert artifact["size"] == len(b"artifact payload")
    assert artifact["content_type"] == "text/plain"
    assert len(artifact["sha256"]) == 64
    assert artifact_service.count() == 1


def test_unauthorized_upload_is_rejected_without_mutation(client):
    agent_id = registry.register("worker", "worker.processor")

    response = client.post(
        f"/api/v2/agents/{agent_id}/artifacts/output.log",
        content=b"artifact payload",
    )

    assert response.status_code == 401
    assert artifact_service.count() == 0


def test_malformed_artifact_name_fails_before_lookup_or_body_read():
    service = ArtifactIngestionService()
    lookups = []

    async def read_body(_max_body_bytes):
        raise AssertionError("body should not be read")

    with pytest.raises(Exception) as exc_info:
        asyncio.run(
            service.ingest(
                agent_id="agent-1",
                artifact_name="..",
                content_length="5",
                content_type="text/plain",
                read_body=read_body,
                agent_exists=(
                    lambda candidate: lookups.append(candidate) or True
                ),
            )
        )

    assert getattr(exc_info.value, "status_code", None) == 400
    assert lookups == []
    assert service.count() == 0


def test_declared_oversized_upload_fails_before_lookup_or_body_read():
    service = ArtifactIngestionService(max_body_bytes=8)
    lookups = []

    async def read_body(_max_body_bytes):
        raise AssertionError("body should not be read")

    with pytest.raises(Exception) as exc_info:
        asyncio.run(
            service.ingest(
                agent_id="agent-1",
                artifact_name="output.log",
                content_length="9",
                content_type="text/plain",
                read_body=read_body,
                agent_exists=(
                    lambda candidate: lookups.append(candidate) or True
                ),
            )
        )

    assert getattr(exc_info.value, "status_code", None) == 413
    assert lookups == []
    assert service.count() == 0


def test_streaming_oversized_upload_stops_before_lookup_or_mutation():
    service = ArtifactIngestionService(max_body_bytes=8)
    lookups = []

    async def read_body(max_body_bytes):
        async def chunks():
            yield b"1234"
            yield b"56789"

        return await read_limited_body(chunks(), max_body_bytes)

    with pytest.raises(Exception) as exc_info:
        asyncio.run(
            service.ingest(
                agent_id="agent-1",
                artifact_name="output.log",
                content_length=None,
                content_type="text/plain",
                read_body=read_body,
                agent_exists=(
                    lambda candidate: lookups.append(candidate) or True
                ),
            )
        )

    assert getattr(exc_info.value, "status_code", None) == 413
    assert lookups == []
    assert service.count() == 0


def test_unknown_agent_fails_without_storing_artifact(client):
    response = client.post(
        "/api/v2/agents/missing-agent/artifacts/output.log",
        content=b"artifact payload",
        headers=auth_headers(),
    )

    assert response.status_code == 404
    assert artifact_service.count() == 0


def test_content_length_mismatch_is_rejected_without_mutation():
    service = ArtifactIngestionService()

    async def read_body(_max_body_bytes):
        return b"abc"

    with pytest.raises(Exception) as exc_info:
        asyncio.run(
            service.ingest(
                agent_id="agent-1",
                artifact_name="output.log",
                content_length="4",
                content_type="text/plain",
                read_body=read_body,
                agent_exists=lambda _candidate: True,
            )
        )

    assert getattr(exc_info.value, "status_code", None) == 400
    assert service.count() == 0
