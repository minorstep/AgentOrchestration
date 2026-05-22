import pytest
from fastapi.testclient import TestClient

from src.api.server import create_app
from src.api.webhooks import (
    WebhookDeliveryManager,
    WebhookDeliveryRejected,
    WebhookEndpointNotFound,
    webhook_delivery_manager,
)
from src.common.metrics import MetricsCollector


class TestWebhookDeliveryManager:
    def setup_method(self):
        self.metrics = MetricsCollector()
        self.manager = WebhookDeliveryManager(metrics_collector=self.metrics)
        self.manager.register_endpoint(
            "workspace-a",
            "endpoint-1",
            "https://hooks.example.com/a",
            secret_version="v1",
        )

    def test_valid_delivery_is_sanitized(self):
        delivery = self.manager.deliver(
            "workspace-a",
            "endpoint-1",
            "event-1",
            {
                "status": "ready",
                "_trace_id": "internal-trace",
                "metadata": {"worker": "private"},
            },
            idempotency_key="event-1:v1",
            sequence=1,
            endpoint_version="v1",
        )

        assert delivery["status"] == "queued"
        callbacks = self.manager.callback_records()
        assert len(callbacks) == 1
        assert callbacks[0]["headers"]["Idempotency-Key"] == "event-1:v1"
        assert callbacks[0]["payload"] == {"status": "ready"}
        assert "internal-trace" not in repr(callbacks)

    def test_retry_with_same_idempotency_key_does_not_duplicate(self):
        first = self.manager.deliver(
            "workspace-a",
            "endpoint-1",
            "event-1",
            {"status": "ready"},
            idempotency_key="event-1:v1",
            sequence=1,
        )
        second = self.manager.deliver(
            "workspace-a",
            "endpoint-1",
            "event-1",
            {"status": "changed"},
            idempotency_key="event-1:v1",
            sequence=1,
        )

        assert second["delivery_id"] == first["delivery_id"]
        assert second["idempotent_replay"]
        assert len(self.manager.delivery_records()) == 1
        assert len(self.manager.callback_records()) == 1
        assert self.manager.callback_records()[0]["payload"] == {
            "status": "ready"
        }

    def test_callback_records_terminal_status_once(self):
        delivery = self.manager.deliver(
            "workspace-a",
            "endpoint-1",
            "event-1",
            {"status": "ready"},
            idempotency_key="event-1:v1",
            sequence=1,
            endpoint_version="v1",
        )
        callback = self.manager.record_callback(
            "workspace-a",
            "endpoint-1",
            "event-1:v1",
            "delivered",
            endpoint_version="v1",
        )
        duplicate = self.manager.record_callback(
            "workspace-a",
            "endpoint-1",
            "event-1:v1",
            "failed",
            endpoint_version="v1",
        )

        assert callback["delivery_id"] == delivery["delivery_id"]
        assert callback["status"] == "delivered"
        assert duplicate["status"] == "delivered"
        assert duplicate["idempotent_replay"]
        assert len(self.manager.callback_receipts()) == 1

    def test_rejects_disabled_rotated_and_stale_deliveries(self):
        self.manager.deliver(
            "workspace-a",
            "endpoint-1",
            "event-1",
            {"status": "ready"},
            idempotency_key="event-1:v1",
            sequence=5,
            endpoint_version="v1",
        )

        with pytest.raises(WebhookDeliveryRejected):
            self.manager.deliver(
                "workspace-a",
                "endpoint-1",
                "event-old",
                {"status": "old"},
                idempotency_key="event-old:v1",
                sequence=4,
                endpoint_version="v1",
            )

        self.manager.rotate_endpoint("workspace-a", "endpoint-1", "v2")
        with pytest.raises(WebhookDeliveryRejected):
            self.manager.deliver(
                "workspace-a",
                "endpoint-1",
                "event-2",
                {"status": "ready"},
                idempotency_key="event-2:v1",
                sequence=6,
                endpoint_version="v1",
            )

        self.manager.disable_endpoint("workspace-a", "endpoint-1")
        with pytest.raises(WebhookDeliveryRejected):
            self.manager.deliver(
                "workspace-a",
                "endpoint-1",
                "event-3",
                {"status": "ready"},
                idempotency_key="event-3:v2",
                sequence=6,
                endpoint_version="v2",
            )

    def test_callback_rejects_rotated_endpoint_version(self):
        self.manager.deliver(
            "workspace-a",
            "endpoint-1",
            "event-1",
            {"status": "ready"},
            idempotency_key="event-1:v1",
            sequence=1,
            endpoint_version="v1",
        )
        self.manager.rotate_endpoint("workspace-a", "endpoint-1", "v2")

        with pytest.raises(WebhookDeliveryRejected):
            self.manager.record_callback(
                "workspace-a",
                "endpoint-1",
                "event-1:v1",
                "delivered",
                endpoint_version="v2",
            )

    def test_workspace_isolation_blocks_cross_workspace_delivery(self):
        with pytest.raises(WebhookEndpointNotFound):
            self.manager.deliver(
                "workspace-b",
                "endpoint-1",
                "event-1",
                {"status": "ready"},
                idempotency_key="event-1:v1",
                sequence=1,
            )

        assert self.manager.callback_records() == []
        assert "workspace-b" not in repr(self.manager.audit_records())

    def test_invalid_endpoint_url_is_rejected(self):
        with pytest.raises(ValueError):
            self.manager.register_endpoint(
                "workspace-a",
                "endpoint-http",
                "http://hooks.example.com/insecure",
            )


def reset_route_manager():
    webhook_delivery_manager._endpoints.clear()
    webhook_delivery_manager._deliveries.clear()
    webhook_delivery_manager._callbacks.clear()
    webhook_delivery_manager._callback_receipts.clear()
    webhook_delivery_manager._audit.clear()


def test_webhook_routes_are_sanitized_and_idempotent():
    reset_route_manager()
    client = TestClient(create_app())
    headers = {"Authorization": "Bearer test-token"}

    registered = client.post(
        "/api/v2/webhooks/endpoints",
        headers=headers,
        params={
            "workspace_id": "workspace-route-secret",
            "endpoint_id": "endpoint-route-secret",
            "url": "https://hooks.example.com/route",
            "secret_version": "v1",
        },
    )
    assert registered.status_code == 200
    assert "workspace-route-secret" not in repr(registered.json())
    assert "endpoint-route-secret" not in repr(registered.json())
    assert "hooks.example.com" not in repr(registered.json())

    first = client.post(
        "/api/v2/webhooks/deliver",
        headers=headers,
        params={
            "workspace_id": "workspace-route-secret",
            "endpoint_id": "endpoint-route-secret",
            "event_id": "event-route-secret",
            "sequence": 1,
            "endpoint_version": "v1",
        },
    )
    retry = client.post(
        "/api/v2/webhooks/deliver",
        headers=headers,
        params={
            "workspace_id": "workspace-route-secret",
            "endpoint_id": "endpoint-route-secret",
            "event_id": "event-route-secret",
            "sequence": 1,
            "endpoint_version": "v1",
        },
    )

    assert first.status_code == 200
    assert retry.status_code == 200
    first_body = first.json()
    retry_body = retry.json()
    assert first_body["idempotency_key"] == retry_body["idempotency_key"]
    assert retry_body["status"] == "queued"
    assert "event-route-secret" not in repr(retry_body)

    callback = client.post(
        "/api/v2/webhooks/callbacks",
        headers=headers,
        params={
            "workspace_id": "workspace-route-secret",
            "endpoint_id": "endpoint-route-secret",
            "idempotency_key": first_body["idempotency_key"],
            "status": "delivered",
            "endpoint_version": "v1",
        },
    )
    duplicate_callback = client.post(
        "/api/v2/webhooks/callbacks",
        headers=headers,
        params={
            "workspace_id": "workspace-route-secret",
            "endpoint_id": "endpoint-route-secret",
            "idempotency_key": first_body["idempotency_key"],
            "status": "failed",
            "endpoint_version": "v1",
        },
    )

    assert callback.status_code == 200
    assert duplicate_callback.status_code == 200
    assert callback.json()["status"] == "delivered"
    assert duplicate_callback.json()["status"] == "delivered"
    assert len(webhook_delivery_manager.callback_receipts()) == 1
