import pytest

from src.orchestrator.webhooks import (
    WebhookDeliveryError,
    WebhookService,
    WebhookValidationError,
)


class PayloadProbe:
    def items(self):
        raise AssertionError("payload was parsed before endpoint trust checks")


def test_production_registration_rejects_non_tls_before_persistence():
    service = WebhookService(production=True)

    with pytest.raises(WebhookValidationError, match="HTTPS"):
        service.register_endpoint(
            "workspace-a",
            "endpoint-1",
            "http://example.com/hook",
        )

    assert service.endpoint_count == 0


def test_development_registration_allows_http_with_absolute_host():
    service = WebhookService(production=False)

    endpoint = service.register_endpoint(
        "workspace-a",
        "endpoint-1",
        " http://example.test/hook ",
    )

    assert endpoint.url == "http://example.test/hook"


def test_registration_rejects_urls_with_embedded_credentials():
    service = WebhookService(production=True)

    with pytest.raises(WebhookValidationError, match="credentials"):
        service.register_endpoint(
            "workspace-a",
            "endpoint-1",
            "https://user:pass@example.com/hook",
        )

    assert service.endpoint_count == 0


def test_valid_delivery_sanitizes_payload_and_records_idempotently():
    callbacks = []
    service = WebhookService(
        production=True,
        delivery_callback=callbacks.append,
    )
    service.register_endpoint(
        "workspace-a",
        "endpoint-1",
        "https://example.com/hook",
    )

    record = service.deliver(
        "workspace-a",
        "endpoint-1",
        "event-1",
        {
            "type": "run.completed",
            "trace_id": "internal-trace",
            "_debug": "hidden",
            "data": {
                "run_id": "run-1",
                "workspace_secret": "secret",
                "_worker": "internal",
            },
            "items": [
                {
                    "name": "public",
                    "internal_metadata": {"node": "worker-1"},
                },
            ],
        },
    )
    retry_record = service.retry(
        "workspace-a",
        "endpoint-1",
        "event-1",
        {"type": "run.completed", "trace_id": "second-trace"},
    )

    expected_payload = {
        "type": "run.completed",
        "data": {"run_id": "run-1"},
        "items": [{"name": "public"}],
    }
    assert retry_record is record
    assert service.delivery_count == 1
    assert len(callbacks) == 1
    assert record.status == "delivered"
    assert record.attempts == 1
    assert record.payload == expected_payload
    assert callbacks[0]["payload"] == expected_payload


def test_workspace_isolation_rejects_before_payload_parsing():
    callbacks = []
    service = WebhookService(
        production=True,
        delivery_callback=callbacks.append,
    )
    service.register_endpoint(
        "workspace-a",
        "endpoint-1",
        "https://example.com/hook",
    )

    with pytest.raises(WebhookDeliveryError, match="not found"):
        service.deliver(
            "workspace-b",
            "endpoint-1",
            "event-1",
            PayloadProbe(),
        )

    assert callbacks == []
    assert service.delivery_count == 0


def test_disabled_endpoint_rejects_before_payload_parsing():
    callbacks = []
    service = WebhookService(
        production=True,
        delivery_callback=callbacks.append,
    )
    service.register_endpoint(
        "workspace-a",
        "endpoint-1",
        "https://example.com/hook",
    )
    service.disable_endpoint("workspace-a", "endpoint-1")

    with pytest.raises(WebhookDeliveryError, match="disabled"):
        service.deliver(
            "workspace-a",
            "endpoint-1",
            "event-1",
            PayloadProbe(),
        )

    assert callbacks == []
    assert service.delivery_count == 0


def test_rotated_endpoint_rejects_stale_version_before_payload_parsing():
    callbacks = []
    service = WebhookService(
        production=True,
        delivery_callback=callbacks.append,
    )
    endpoint = service.register_endpoint(
        "workspace-a",
        "endpoint-1",
        "https://example.com/hook",
    )
    rotated = service.rotate_endpoint(
        "workspace-a",
        "endpoint-1",
        "https://hooks.example.com/new",
    )

    with pytest.raises(WebhookDeliveryError, match="rotated"):
        service.deliver(
            "workspace-a",
            "endpoint-1",
            "event-1",
            PayloadProbe(),
            endpoint_version=endpoint.version,
        )

    record = service.deliver(
        "workspace-a",
        "endpoint-1",
        "event-1",
        {"type": "run"},
        endpoint_version=rotated.version,
    )

    assert callbacks[0]["url"] == "https://hooks.example.com/new"
    assert record.endpoint_version == rotated.version
    assert service.delivery_count == 1
