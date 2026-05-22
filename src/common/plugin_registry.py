"""Plugin capability registry."""

import logging
import time
from typing import Any, Callable, Dict, List, Mapping, Optional

from src.common.metrics import metrics

logger = logging.getLogger(__name__)


class PluginRegistry:
    """Tracks plugin capabilities and rejects ambiguous registrations."""

    def __init__(self):
        self._plugins: Dict[str, Dict[str, Any]] = {}
        self._capability_index: Dict[str, str] = {}
        self._resolution_cache: Dict[str, Dict[str, Any]] = {}
        self._audit: List[Dict[str, Any]] = []

    def register_plugin(
        self,
        plugin_id: str,
        capabilities: Mapping[str, Callable[..., Any]],
        *,
        tenant_id: Optional[str] = None,
        version: Optional[str] = None,
    ) -> bool:
        plugin_ref = self._safe_ref(plugin_id)
        normalised = self._normalise_capabilities(capabilities)

        duplicate = self._first_duplicate_capability(normalised)
        if duplicate:
            self._record(
                plugin_id=plugin_ref,
                capability=duplicate,
                accepted=False,
                reason="duplicate_capability",
                tenant_id=tenant_id,
            )
            metrics.increment("plugin_registry.registration.rejected")
            logger.warning(
                "Rejected duplicate plugin capability",
                extra={
                    "plugin_id": plugin_ref,
                    "capability": duplicate,
                    "reason": "duplicate_capability",
                },
            )
            return False

        if plugin_ref in self._plugins:
            self._record(
                plugin_id=plugin_ref,
                capability=None,
                accepted=False,
                reason="plugin_already_registered",
                tenant_id=tenant_id,
            )
            metrics.increment("plugin_registry.registration.rejected")
            return False

        self._plugins[plugin_ref] = {
            "capabilities": normalised,
            "tenant_id": tenant_id,
            "version": version,
            "registered_at": time.time(),
        }
        for capability in normalised:
            self._capability_index[capability] = plugin_ref
            self._resolution_cache.pop(capability, None)
            self._record(
                plugin_id=plugin_ref,
                capability=capability,
                accepted=True,
                reason="registered",
                tenant_id=tenant_id,
            )
        metrics.increment("plugin_registry.registration.accepted")
        return True

    def unregister_plugin(self, plugin_id: str) -> bool:
        plugin_ref = self._safe_ref(plugin_id)
        plugin = self._plugins.pop(plugin_ref, None)
        if not plugin:
            self._record(
                plugin_id=plugin_ref,
                capability=None,
                accepted=False,
                reason="plugin_not_registered",
                tenant_id=None,
            )
            metrics.increment("plugin_registry.unregister.miss")
            return False

        for capability in plugin["capabilities"]:
            self._capability_index.pop(capability, None)
            self._resolution_cache.pop(capability, None)
            self._record(
                plugin_id=plugin_ref,
                capability=capability,
                accepted=True,
                reason="unregistered",
                tenant_id=plugin.get("tenant_id"),
            )
        metrics.increment("plugin_registry.unregister.accepted")
        return True

    def resolve_capability(self, capability: str) -> Optional[Dict[str, Any]]:
        name = self._normalise_capability_name(capability)
        if name in self._resolution_cache:
            return dict(self._resolution_cache[name])

        plugin_id = self._capability_index.get(name)
        if not plugin_id:
            return None

        plugin = self._plugins[plugin_id]
        resolution = {
            "capability": name,
            "plugin_id": plugin_id,
            "tenant_id": plugin.get("tenant_id"),
            "version": plugin.get("version"),
            "handler": plugin["capabilities"][name],
        }
        self._resolution_cache[name] = resolution
        return dict(resolution)

    def list_capabilities(self) -> Dict[str, Dict[str, Any]]:
        return {
            capability: self._public_plugin_metadata(plugin_id)
            for capability, plugin_id in self._capability_index.items()
        }

    def audit_log(self) -> List[Dict[str, Any]]:
        return [dict(event) for event in self._audit]

    def _first_duplicate_capability(
        self,
        capabilities: Mapping[str, Callable[..., Any]],
    ) -> Optional[str]:
        for capability in capabilities:
            if capability in self._capability_index:
                return capability
        return None

    def _normalise_capabilities(
        self,
        capabilities: Mapping[str, Callable[..., Any]],
    ) -> Dict[str, Callable[..., Any]]:
        if not capabilities:
            raise ValueError("at least one capability is required")

        normalised: Dict[str, Callable[..., Any]] = {}
        for raw_name, handler in capabilities.items():
            name = self._normalise_capability_name(raw_name)
            if name in normalised:
                raise ValueError("duplicate capability name in plugin")
            if not callable(handler):
                raise ValueError("capability handler must be callable")
            normalised[name] = handler
        return normalised

    def _normalise_capability_name(self, value: str) -> str:
        name = str(value or "").strip()
        if not name:
            raise ValueError("capability name is required")
        return name

    def _safe_ref(self, value: str) -> str:
        ref = str(value or "").strip()
        if not ref:
            raise ValueError("plugin id is required")
        return ref[:128]

    def _public_plugin_metadata(self, plugin_id: str) -> Dict[str, Any]:
        plugin = self._plugins[plugin_id]
        return {
            "plugin_id": plugin_id,
            "tenant_id": plugin.get("tenant_id"),
            "version": plugin.get("version"),
            "registered_at": plugin.get("registered_at"),
        }

    def _record(
        self,
        *,
        plugin_id: str,
        capability: Optional[str],
        accepted: bool,
        reason: str,
        tenant_id: Optional[str],
    ) -> None:
        self._audit.append(
            {
                "plugin_id": plugin_id,
                "capability": capability,
                "tenant_id": tenant_id,
                "accepted": accepted,
                "reason": reason,
                "timestamp": time.time(),
            }
        )
