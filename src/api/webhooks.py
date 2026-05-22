"""Webhook endpoint registration and idempotent delivery helpers."""

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from uuid import uuid4

from src.common.metrics import metrics


class WebhookDeliveryError(ValueError):
    """Base error for webhook delivery validation failures."""


class WebhookEndpointNotFound(WebhookDeliveryError):
    """Raised when an endpoint is not registered for the workspace."""


class WebhookDeliveryRejected(WebhookDeliveryError):
    """Raised when an endpoint cannot accept the delivery."""


@dataclass
class WebhookEndpoint:
    workspace_id: str
    endpoint_id: str
    url: str
    secret_version: str
    enabled: bool = True
    last_sequence: int = -1


class WebhookDeliveryManager:
    def __init__(self, metrics_collector=None):
        self._endpoints: Dict[Tuple[str, str], WebhookEndpoint] = {}
        self._deliveries: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        self._callbacks: List[Dict[str, Any]] = []
        self._callback_receipts: List[Dict[str, Any]] = []
        self._audit: List[Dict[str, Any]] = []
        self.metrics = metrics_collector or metrics

    def register_endpoint(
        self,
        workspace_id: str,
        endpoint_id: str,
        url: str,
        secret_version: str = "v1",
        enabled: bool = True,
    ) -> Dict[str, Any]:
        self._validate_ref(workspace_id, "workspace_id")
        self._validate_ref(endpoint_id, "endpoint_id")
        self._validate_url(url)
        endpoint = WebhookEndpoint(
            workspace_id=workspace_id,
            endpoint_id=endpoint_id,
            url=url,
            secret_version=secret_version,
            enabled=enabled,
        )
        self._endpoints[(workspace_id, endpoint_id)] = endpoint
        return self._safe_endpoint(endpoint)

    def disable_endpoint(
        self,
        workspace_id: str,
        endpoint_id: str,
    ) -> Dict[str, Any]:
        endpoint = self._require_endpoint(workspace_id, endpoint_id)
        endpoint.enabled = False
        return self._safe_endpoint(endpoint)

    def rotate_endpoint(
        self,
        workspace_id: str,
        endpoint_id: str,
        secret_version: Optional[str] = None,
        url: Optional[str] = None,
    ) -> Dict[str, Any]:
        endpoint = self._require_endpoint(workspace_id, endpoint_id)
        if url is not None:
            self._validate_url(url)
            endpoint.url = url
        endpoint.secret_version = (
            secret_version
            or self._next_secret_version(endpoint.secret_version)
        )
        endpoint.enabled = True
        return self._safe_endpoint(endpoint)

    def deliver(
        self,
        workspace_id: str,
        endpoint_id: str,
        event_id: str,
        payload: Dict[str, Any],
        *,
        idempotency_key: Optional[str] = None,
        sequence: Optional[int] = None,
        endpoint_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        endpoint = self._require_endpoint(workspace_id, endpoint_id)
        key = idempotency_key or self._make_idempotency_key(
            workspace_id,
            endpoint_id,
            endpoint.secret_version,
            event_id,
        )
        delivery_key = (workspace_id, endpoint_id, key)

        existing = self._deliveries.get(delivery_key)
        if existing:
            self._record("replayed", endpoint, key)
            return {**existing, "idempotent_replay": True}

        self._validate_endpoint(endpoint, endpoint_version)
        if sequence is not None and sequence <= endpoint.last_sequence:
            self._record("stale_sequence_rejected", endpoint, key)
            raise WebhookDeliveryRejected("delivery sequence is stale")

        delivery = {
            "delivery_id": str(uuid4()),
            "event_id": event_id,
            "endpoint_id": endpoint_id,
            "workspace_id": workspace_id,
            "idempotency_key": key,
            "endpoint_version": endpoint.secret_version,
            "sequence": sequence,
            "status": "queued",
        }
        self._deliveries[delivery_key] = delivery
        if sequence is not None:
            endpoint.last_sequence = sequence
        self._callbacks.append(
            {
                "url": endpoint.url,
                "headers": {
                    "Idempotency-Key": key,
                    "X-Event-ID": event_id,
                },
                "payload": self._public_payload(payload),
            }
        )
        self._record("delivered", endpoint, key)
        return {**delivery, "idempotent_replay": False}

    def record_callback(
        self,
        workspace_id: str,
        endpoint_id: str,
        idempotency_key: str,
        status: str,
        *,
        endpoint_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        endpoint = self._require_endpoint(workspace_id, endpoint_id)
        delivery_key = (workspace_id, endpoint_id, idempotency_key)
        delivery = self._deliveries.get(delivery_key)
        if not delivery:
            self._record(
                "callback_missing_delivery",
                endpoint,
                idempotency_key,
            )
            raise WebhookDeliveryRejected("callback delivery record not found")
        if (
            endpoint_version is not None
            and endpoint_version != delivery["endpoint_version"]
        ):
            self._record(
                "callback_version_rejected",
                endpoint,
                idempotency_key,
            )
            raise WebhookDeliveryRejected("callback endpoint version is stale")
        if delivery["status"] in {"delivered", "failed"}:
            self._record("callback_replayed", endpoint, idempotency_key)
            return {**delivery, "idempotent_replay": True}
        if status not in {"delivered", "failed"}:
            self._record("callback_status_rejected", endpoint, idempotency_key)
            raise WebhookDeliveryRejected("callback status is invalid")

        delivery["status"] = status
        receipt = {
            "delivery_id": delivery["delivery_id"],
            "status": status,
            "workspace_ref": self._hash_ref(workspace_id),
            "endpoint_ref": self._hash_ref(endpoint_id),
        }
        self._callback_receipts.append(receipt)
        self._record("callback_recorded", endpoint, idempotency_key)
        return {**delivery, "idempotent_replay": False}

    def callback_records(self) -> List[Dict[str, Any]]:
        return [dict(record) for record in self._callbacks]

    def callback_receipts(self) -> List[Dict[str, Any]]:
        return [dict(record) for record in self._callback_receipts]

    def delivery_records(self) -> List[Dict[str, Any]]:
        return [dict(record) for record in self._deliveries.values()]

    def public_delivery(
        self,
        workspace_id: str,
        endpoint_id: str,
        idempotency_key: str,
    ) -> Optional[Dict[str, Any]]:
        delivery = self._deliveries.get(
            (workspace_id, endpoint_id, idempotency_key)
        )
        if not delivery:
            return None
        return self._safe_delivery(delivery)

    def audit_records(self) -> List[Dict[str, Any]]:
        return [dict(record) for record in self._audit]

    def _require_endpoint(
        self,
        workspace_id: str,
        endpoint_id: str,
    ) -> WebhookEndpoint:
        endpoint = self._endpoints.get((workspace_id, endpoint_id))
        if not endpoint:
            raise WebhookEndpointNotFound("webhook endpoint not found")
        return endpoint

    def _validate_endpoint(
        self,
        endpoint: WebhookEndpoint,
        endpoint_version: Optional[str],
    ) -> None:
        if not endpoint.enabled:
            self._record("disabled_rejected", endpoint, endpoint.endpoint_id)
            raise WebhookDeliveryRejected("webhook endpoint is disabled")
        if endpoint_version and endpoint_version != endpoint.secret_version:
            self._record("rotated_rejected", endpoint, endpoint.endpoint_id)
            raise WebhookDeliveryRejected("webhook endpoint version is stale")

    def _safe_endpoint(self, endpoint: WebhookEndpoint) -> Dict[str, Any]:
        return {
            "workspace_ref": self._hash_ref(endpoint.workspace_id),
            "endpoint_ref": self._hash_ref(endpoint.endpoint_id),
            "endpoint_version": endpoint.secret_version,
            "enabled": endpoint.enabled,
        }

    def _safe_delivery(self, delivery: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "delivery_id": delivery["delivery_id"],
            "idempotency_key": delivery["idempotency_key"],
            "status": delivery["status"],
            "sequence": delivery["sequence"],
            "endpoint_version": delivery["endpoint_version"],
            "workspace_ref": self._hash_ref(delivery["workspace_id"]),
            "endpoint_ref": self._hash_ref(delivery["endpoint_id"]),
            "event_ref": self._hash_ref(delivery["event_id"]),
        }

    def _record(
        self,
        decision: str,
        endpoint: WebhookEndpoint,
        key: str,
    ) -> None:
        digest = hashlib.sha256(
            f"{endpoint.workspace_id}:{endpoint.endpoint_id}:{key}".encode()
        ).hexdigest()
        self._audit.append(
            {
                "decision": decision,
                "endpoint_ref": digest[:12],
                "workspace_ref": self._hash_ref(endpoint.workspace_id),
            }
        )
        self.metrics.increment(f"webhook.delivery.{decision}")

    @staticmethod
    def _hash_ref(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()[:12]

    @staticmethod
    def _make_idempotency_key(
        workspace_id: str,
        endpoint_id: str,
        endpoint_version: str,
        event_id: str,
    ) -> str:
        raw = f"{workspace_id}:{endpoint_id}:{endpoint_version}:{event_id}"
        return hashlib.sha256(
            raw.encode()
        ).hexdigest()

    @staticmethod
    def _next_secret_version(version: str) -> str:
        if version.startswith("v") and version[1:].isdigit():
            return f"v{int(version[1:]) + 1}"
        return f"{version}:rotated"

    @staticmethod
    def _validate_ref(value: str, field: str) -> None:
        if not value or not value.strip():
            raise ValueError(f"{field} is required")

    @staticmethod
    def _validate_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("webhook endpoint must be an https URL")

    @staticmethod
    def _public_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: value
            for key, value in payload.items()
            if not key.startswith("_") and key not in {"internal", "metadata"}
        }


webhook_delivery_manager = WebhookDeliveryManager()
