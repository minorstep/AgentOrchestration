"""Plugin manifest validation and hook loading."""

import importlib
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from threading import RLock
from typing import Any, Callable, Dict, List, Optional, cast


class PluginRuntimeState(Enum):
    PENDING = "pending"
    VALIDATING = "validating"
    REJECTED = "rejected"
    LOADING = "loading"
    LOADED = "loaded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PluginManifestError(ValueError):
    def __init__(self, reason: str, plugin_id: str = "unknown"):
        self.reason = reason
        self.plugin_id = plugin_id
        super().__init__(f"{plugin_id}: {reason}")


@dataclass
class PluginLoadRecord:
    plugin_id: str
    state: PluginRuntimeState = PluginRuntimeState.PENDING
    attempts: int = 0
    error: Optional[str] = None
    hooks: List[str] = field(default_factory=list)


class PluginRuntime:
    ALLOWED_EVENTS = frozenset(
        {"pre_execute", "post_execute", "on_error", "on_complete"}
    )
    TERMINAL_STATES = frozenset(
        {
            PluginRuntimeState.REJECTED,
            PluginRuntimeState.LOADED,
            PluginRuntimeState.FAILED,
            PluginRuntimeState.CANCELLED,
        }
    )
    IN_PROGRESS_STATES = frozenset(
        {
            PluginRuntimeState.VALIDATING,
            PluginRuntimeState.LOADING,
        }
    )

    def __init__(
        self,
        engine,
        importer: Optional[Callable[[str], Callable[..., Any]]] = None,
        max_hooks: int = 32,
        max_attempts: int = 2,
    ):
        self.engine = engine
        self.importer = importer or self._import_callback
        self.max_hooks = max_hooks
        self.max_attempts = max_attempts
        self._lock = RLock()
        self._records: Dict[str, PluginLoadRecord] = {}
        self._audit_log: List[Dict[str, str]] = []

    @property
    def audit_log(self) -> List[Dict[str, str]]:
        with self._lock:
            return deepcopy(self._audit_log)

    def get_record(self, plugin_id: str) -> Optional[PluginLoadRecord]:
        with self._lock:
            record = self._records.get(plugin_id)
            return deepcopy(record) if record else None

    def cancel(self, plugin_id: str) -> bool:
        with self._lock:
            record = self._records.setdefault(
                plugin_id,
                PluginLoadRecord(plugin_id=plugin_id),
            )
            if record.state in self.TERMINAL_STATES:
                return False
            record.state = PluginRuntimeState.CANCELLED
            record.error = "cancelled"
            self._record_audit(plugin_id, "cancelled")
            return True

    def load_manifest(self, manifest: Dict[str, Any]) -> bool:
        plugin_id = self._preview_plugin_id(manifest)
        with self._lock:
            record = self._records.setdefault(
                plugin_id,
                PluginLoadRecord(plugin_id=plugin_id),
            )
            if record.state is PluginRuntimeState.LOADED:
                self._record_audit(plugin_id, "load_idempotent")
                return True
            if record.state in self.IN_PROGRESS_STATES:
                self._record_audit(plugin_id, "load_in_progress")
                return False
            if record.state in self.TERMINAL_STATES:
                self._record_audit(plugin_id, "terminal_state_reused")
                return False
            if record.attempts >= self.max_attempts:
                record.state = PluginRuntimeState.FAILED
                record.error = "retry_limit_exceeded"
                self._record_audit(plugin_id, "retry_limit_exceeded")
                return False

            record.state = PluginRuntimeState.VALIDATING
            self._record_audit(plugin_id, "validating")

            try:
                validated = self._validate_manifest(manifest)
            except PluginManifestError as exc:
                record.state = PluginRuntimeState.REJECTED
                record.error = exc.reason
                self._record_audit(plugin_id, "rejected", exc.reason)
                return False

            record.state = PluginRuntimeState.LOADING
            record.attempts += 1
            self._record_audit(plugin_id, "loading")

        try:
            callbacks = [
                (hook["event"], hook["id"], self.importer(hook["callback"]))
                for hook in validated["hooks"]
            ]
            for _, _, callback in callbacks:
                if not callable(callback):
                    raise PluginManifestError(
                        "hook_callback_not_callable",
                        plugin_id,
                    )
        except Exception:
            with self._lock:
                record.state = PluginRuntimeState.FAILED
                record.error = "hook_import_failed"
                self._record_audit(plugin_id, "failed", "hook_import_failed")
            return False

        with self._lock:
            if record.state is PluginRuntimeState.CANCELLED:
                self._record_audit(plugin_id, "load_cancelled")
                return False
            for event, hook_id, callback in callbacks:
                self.engine.register_hook(event, callback)
                record.hooks.append(hook_id)
            record.state = PluginRuntimeState.LOADED
            record.error = None
            self._record_audit(plugin_id, "loaded")
            return True

    def _validate_manifest(self, manifest: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(manifest, dict):
            raise PluginManifestError("manifest_must_be_object")

        plugin_id = self._preview_plugin_id(manifest)
        version = manifest.get("version")
        hooks = manifest.get("hooks")
        if not plugin_id or plugin_id == "unknown":
            raise PluginManifestError("plugin_id_required", plugin_id)
        if not isinstance(version, str) or not version.strip():
            raise PluginManifestError("version_required", plugin_id)
        if not isinstance(hooks, list) or not hooks:
            raise PluginManifestError("hooks_required", plugin_id)
        if len(hooks) > self.max_hooks:
            raise PluginManifestError("too_many_hooks", plugin_id)

        seen_hook_ids = set()
        validated_hooks = []
        for index, hook in enumerate(hooks):
            if not isinstance(hook, dict):
                raise PluginManifestError("hook_must_be_object", plugin_id)
            event = hook.get("event")
            callback = hook.get("callback")
            hook_id = str(hook.get("id") or f"{event}:{callback}")
            if hook_id in seen_hook_ids:
                raise PluginManifestError("duplicate_hook_id", plugin_id)
            seen_hook_ids.add(hook_id)
            if event not in self.ALLOWED_EVENTS:
                raise PluginManifestError("unsupported_hook_event", plugin_id)
            if not self._valid_callback_ref(callback):
                raise PluginManifestError("invalid_hook_callback", plugin_id)
            validated_hooks.append(
                {
                    "id": hook_id,
                    "event": event,
                    "callback": callback,
                    "index": index,
                }
            )

        return {
            "id": plugin_id,
            "version": version.strip(),
            "hooks": validated_hooks,
        }

    def _preview_plugin_id(self, manifest: Any) -> str:
        if isinstance(manifest, dict):
            plugin_id = manifest.get("id") or manifest.get("name")
            if isinstance(plugin_id, str) and plugin_id.strip():
                return plugin_id.strip()
        return "unknown"

    def _valid_callback_ref(self, value: Any) -> bool:
        if not isinstance(value, str) or not value.strip():
            return False
        module_name, _, function_name = value.partition(":")
        return bool(module_name.strip() and function_name.strip())

    def _import_callback(self, reference: str) -> Callable[..., Any]:
        module_name, _, function_name = reference.partition(":")
        module = importlib.import_module(module_name)
        callback: Any = module
        for part in function_name.split("."):
            callback = getattr(callback, part)
        return cast(Callable[..., Any], callback)

    def _record_audit(
        self,
        plugin_id: str,
        event: str,
        reason: Optional[str] = None,
    ) -> None:
        record = {"plugin_id": plugin_id, "event": event}
        if reason:
            record["reason"] = reason
        self._audit_log.append(record)
