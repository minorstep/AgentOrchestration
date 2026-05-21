"""Webhook registration and delivery trust boundaries."""

from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Tuple
from urllib.parse import urlparse


class WebhookValidationError(ValueError):
    """Raised when a webhook endpoint fails registration validation."""


class WebhookDeliveryError(ValueError):
    """Raised when a webhook delivery is not safe to dispatch."""


@dataclass(frozen=True)
class WebhookEndpoint:
    workspace_id: str
    endpoint_id: str
    url: str
    enabled: bool = True
    version: int = 1


@dataclass(frozen=True)
class DeliveryRecord:
    workspace_id: str
    endpoint_id: str
    event_id: str
    endpoint_version: int
    status: str
    attempts: int
    callback_url: str
    payload: Dict[str, Any]


class WebhookService:
    INTERNAL_FIELDS = {
        "internal_id",
        "internal_metadata",
        "retry_token",
        "trace_id",
        "workspace_secret",
    }

    def __init__(
        self,
        production: bool = True,
        delivery_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.production = production
        self._delivery_callback = delivery_callback
        self._endpoints: Dict[Tuple[str, str], WebhookEndpoint] = {}
        self._deliveries: Dict[Tuple[str, str, str], DeliveryRecord] = {}

    @property
    def endpoint_count(self) -> int:
        return len(self._endpoints)

    @property
    def delivery_count(self) -> int:
        return len(self._deliveries)

    def register_endpoint(
        self,
        workspace_id: str,
        endpoint_id: str,
        url: str,
        enabled: bool = True,
    ) -> WebhookEndpoint:
        workspace_id = self._required_id(workspace_id, "workspace_id")
        endpoint_id = self._required_id(endpoint_id, "endpoint_id")
        clean_url = self._validate_url(url)
        endpoint = WebhookEndpoint(
            workspace_id=workspace_id,
            endpoint_id=endpoint_id,
            url=clean_url,
            enabled=enabled,
        )
        self._endpoints[(workspace_id, endpoint_id)] = endpoint
        return endpoint

    def disable_endpoint(self, workspace_id: str, endpoint_id: str) -> None:
        endpoint = self._get_endpoint(workspace_id, endpoint_id)
        disabled = WebhookEndpoint(
            workspace_id=endpoint.workspace_id,
            endpoint_id=endpoint.endpoint_id,
            url=endpoint.url,
            enabled=False,
            version=endpoint.version,
        )
        key = (endpoint.workspace_id, endpoint.endpoint_id)
        self._endpoints[key] = disabled

    def rotate_endpoint(
        self,
        workspace_id: str,
        endpoint_id: str,
        url: str,
    ) -> WebhookEndpoint:
        endpoint = self._get_endpoint(workspace_id, endpoint_id)
        clean_url = self._validate_url(url)
        rotated = WebhookEndpoint(
            workspace_id=endpoint.workspace_id,
            endpoint_id=endpoint.endpoint_id,
            url=clean_url,
            enabled=endpoint.enabled,
            version=endpoint.version + 1,
        )
        key = (endpoint.workspace_id, endpoint.endpoint_id)
        self._endpoints[key] = rotated
        return rotated

    def deliver(
        self,
        workspace_id: str,
        endpoint_id: str,
        event_id: str,
        payload: Mapping[str, Any],
        endpoint_version: Optional[int] = None,
    ) -> DeliveryRecord:
        endpoint = self._get_deliverable_endpoint(
            workspace_id,
            endpoint_id,
            endpoint_version,
        )
        event_id = self._required_id(event_id, "event_id")
        delivery_key = (endpoint.workspace_id, endpoint.endpoint_id, event_id)
        if delivery_key in self._deliveries:
            return self._deliveries[delivery_key]

        public_payload = self._public_payload(payload)
        callback = {
            "endpoint_id": endpoint.endpoint_id,
            "event_id": event_id,
            "url": endpoint.url,
            "payload": public_payload,
        }
        if self._delivery_callback:
            self._delivery_callback(callback)

        record = DeliveryRecord(
            workspace_id=endpoint.workspace_id,
            endpoint_id=endpoint.endpoint_id,
            event_id=event_id,
            endpoint_version=endpoint.version,
            status="delivered",
            attempts=1,
            callback_url=endpoint.url,
            payload=public_payload,
        )
        self._deliveries[delivery_key] = record
        return record

    def retry(
        self,
        workspace_id: str,
        endpoint_id: str,
        event_id: str,
        payload: Mapping[str, Any],
        endpoint_version: Optional[int] = None,
    ) -> DeliveryRecord:
        return self.deliver(
            workspace_id,
            endpoint_id,
            event_id,
            payload,
            endpoint_version=endpoint_version,
        )

    def _get_endpoint(
        self,
        workspace_id: str,
        endpoint_id: str,
    ) -> WebhookEndpoint:
        workspace_id = self._required_id(workspace_id, "workspace_id")
        endpoint_id = self._required_id(endpoint_id, "endpoint_id")
        endpoint = self._endpoints.get((workspace_id, endpoint_id))
        if endpoint is None:
            raise WebhookDeliveryError(
                "Webhook endpoint not found for workspace",
            )
        return endpoint

    def _get_deliverable_endpoint(
        self,
        workspace_id: str,
        endpoint_id: str,
        endpoint_version: Optional[int],
    ) -> WebhookEndpoint:
        endpoint = self._get_endpoint(workspace_id, endpoint_id)
        if not endpoint.enabled:
            raise WebhookDeliveryError("Webhook endpoint is disabled")
        version_is_invalid = (
            endpoint_version is not None
            and not isinstance(endpoint_version, int)
        )
        if version_is_invalid:
            raise WebhookDeliveryError(
                "Webhook endpoint version must be an integer"
            )
        version_is_stale = (
            endpoint_version is not None
            and endpoint.version != endpoint_version
        )
        if version_is_stale:
            raise WebhookDeliveryError("Webhook endpoint has been rotated")
        return endpoint

    def _validate_url(self, url: str) -> str:
        if not isinstance(url, str) or not url.strip():
            raise WebhookValidationError("Webhook URL is required")

        clean_url = url.strip()
        parsed = urlparse(clean_url)
        scheme = parsed.scheme.lower()
        if not scheme or not parsed.netloc:
            raise WebhookValidationError("Webhook URL must be absolute")
        if scheme not in {"http", "https"}:
            raise WebhookValidationError("Webhook URL must use HTTP or HTTPS")
        if self.production and scheme != "https":
            raise WebhookValidationError(
                "Production webhook URLs must use HTTPS",
            )
        if not parsed.hostname:
            raise WebhookValidationError("Webhook URL must include a host")
        if parsed.username or parsed.password:
            raise WebhookValidationError(
                "Webhook URL must not include credentials",
            )
        return clean_url

    def _public_payload(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise WebhookDeliveryError("Webhook payload must be a mapping")
        return self._sanitize_mapping(payload)

    def _sanitize_mapping(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            key: self._sanitize_value(value)
            for key, value in payload.items()
            if self._is_public_field(key)
        }

    def _sanitize_value(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return self._sanitize_mapping(value)
        if isinstance(value, list):
            return [self._sanitize_value(item) for item in value]
        return value

    def _is_public_field(self, key: Any) -> bool:
        return (
            not isinstance(key, str)
            or (not key.startswith("_") and key not in self.INTERNAL_FIELDS)
        )

    @staticmethod
    def _required_id(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise WebhookValidationError(f"{field} is required")
        return value.strip()
