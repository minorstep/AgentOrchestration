import pytest

from src.api.webhooks import (
    WebhookDeliveryManager,
    WebhookDeliveryRejected,
    WebhookEndpointNotFound,
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

        assert delivery["status"] == "delivered"
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
