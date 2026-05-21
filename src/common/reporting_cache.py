"""Authorization-aware reporting result cache."""

import json
import time
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from threading import RLock
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    Optional,
    Tuple,
)


class ReportingAuthorizationError(PermissionError):
    """Raised when current report access no longer allows cached data."""


@dataclass(frozen=True)
class ReportAuthContext:
    user_id: str
    workspace_id: str
    roles: FrozenSet[str]
    permissions: FrozenSet[str]
    access_version: str = "0"

    @classmethod
    def create(
        cls,
        user_id: str,
        workspace_id: str,
        roles: Iterable[Any],
        permissions: Iterable[Any],
        access_version: str = "0",
    ) -> "ReportAuthContext":
        return cls(
            user_id=user_id,
            workspace_id=workspace_id,
            roles=frozenset(str(role).casefold() for role in roles),
            permissions=frozenset(
                str(permission).casefold() for permission in permissions
            ),
            access_version=str(access_version),
        )

    def has_permission(self, permission: str) -> bool:
        return permission.casefold() in self.permissions

    def cache_parts(self) -> Tuple[Any, ...]:
        return (
            self.user_id,
            self.workspace_id,
            tuple(sorted(self.roles)),
            tuple(sorted(self.permissions)),
            self.access_version,
        )


@dataclass
class _ReportCacheEntry:
    payload: Any
    created_at: float


class ReportingCache:
    def __init__(
        self,
        max_entries: int = 256,
        ttl_seconds: Optional[float] = None,
        clock: Callable[[], float] = time.time,
    ):
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._lock = RLock()
        self._entries: Dict[Tuple[Any, ...], _ReportCacheEntry] = {}
        self._audit_log: List[Dict[str, Any]] = []

    @property
    def audit_log(self) -> List[Dict[str, Any]]:
        with self._lock:
            return deepcopy(self._audit_log)

    def get_or_set(
        self,
        query: Mapping[str, Any],
        auth_context: ReportAuthContext,
        producer: Callable[[], Any],
        authorizer: Callable[[ReportAuthContext], bool],
        required_permission: str = "reports:read",
    ) -> Any:
        self._ensure_authorized(
            auth_context,
            authorizer,
            required_permission,
            "read",
        )
        cache_key = self.cache_key(query, auth_context)

        with self._lock:
            entry = self._entries.get(cache_key)
            if entry and not self._expired(entry):
                self._ensure_authorized(
                    auth_context,
                    authorizer,
                    required_permission,
                    "cached_read",
                )
                self._record("hit", auth_context)
                return deepcopy(entry.payload)
            if entry:
                self._entries.pop(cache_key, None)
                self._record("expired", auth_context)

        payload = producer()
        with self._lock:
            self._ensure_authorized(
                auth_context,
                authorizer,
                required_permission,
                "store",
            )
            self._entries[cache_key] = _ReportCacheEntry(
                payload=deepcopy(payload),
                created_at=self.clock(),
            )
            self._evict_oldest()
            self._record("store", auth_context)
            return deepcopy(payload)

    def invalidate_workspace(self, workspace_id: str) -> int:
        with self._lock:
            keys = [
                key
                for key in self._entries
                if len(key) > 2 and key[2] == workspace_id
            ]
            for key in keys:
                self._entries.pop(key, None)
            return len(keys)

    def cache_key(
        self,
        query: Mapping[str, Any],
        auth_context: ReportAuthContext,
    ) -> Tuple[Any, ...]:
        return (
            self._query_digest(query),
            *auth_context.cache_parts(),
        )

    def _ensure_authorized(
        self,
        auth_context: ReportAuthContext,
        authorizer: Callable[[ReportAuthContext], bool],
        required_permission: str,
        event: str,
    ) -> None:
        if (
            not auth_context.has_permission(required_permission)
            or not authorizer(auth_context)
        ):
            with self._lock:
                self._record(f"{event}_denied", auth_context)
            raise ReportingAuthorizationError("report access denied")

    def _query_digest(self, query: Mapping[str, Any]) -> str:
        encoded = json.dumps(query, sort_keys=True, separators=(",", ":"))
        return sha256(encoded.encode()).hexdigest()

    def _expired(self, entry: _ReportCacheEntry) -> bool:
        if self.ttl_seconds is None:
            return False
        return self.clock() - entry.created_at > self.ttl_seconds

    def _evict_oldest(self) -> None:
        while len(self._entries) > self.max_entries:
            oldest_key = min(
                self._entries,
                key=lambda key: self._entries[key].created_at,
            )
            self._entries.pop(oldest_key, None)

    def _record(self, event: str, auth_context: ReportAuthContext) -> None:
        self._audit_log.append(
            {
                "event": event,
                "user_id": auth_context.user_id,
                "workspace_id": auth_context.workspace_id,
                "roles": sorted(auth_context.roles),
                "permissions": sorted(auth_context.permissions),
                "access_version": auth_context.access_version,
            }
        )
