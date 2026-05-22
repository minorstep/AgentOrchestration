"""Shared authorization checks for protected API workflows."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from threading import RLock
from typing import Dict, FrozenSet, Iterable, List, Optional


TASK_MONITOR_SCOPE = "task:monitor"
WORKSPACE_VIEWER_ROLE = "workspace.viewer"
WORKSPACE_ADMIN_ROLE = "workspace.admin"


class AuthError(Exception):
    """Public-safe authorization failure."""

    def __init__(self, status_code: int, reason: str):
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason

    @property
    def public_message(self) -> str:
        return "Unauthorized" if self.status_code == 401 else "Forbidden"


@dataclass(frozen=True)
class AuthContext:
    """Sanitized identity context attached to protected requests."""

    subject: str
    workspace_id: str
    scopes: FrozenSet[str]
    roles: FrozenSet[str]
    client_type: str
    credential_ref: str


@dataclass
class _CredentialRecord:
    context: AuthContext
    revoked: bool = False
    disabled: bool = False
    expires_at: Optional[float] = None


class AuthService:
    """Central, testable credential and permission service."""

    def __init__(self):
        self._lock = RLock()
        self._api_keys: Dict[str, _CredentialRecord] = {}
        self._sessions: Dict[str, _CredentialRecord] = {}
        self._audit_events: List[Dict[str, str]] = []

    def reset(self) -> None:
        with self._lock:
            self._api_keys.clear()
            self._sessions.clear()
            self._audit_events.clear()

    def register_api_key(
        self,
        token: str,
        *,
        subject: str,
        workspace_id: str,
        scopes: Iterable[str],
        roles: Iterable[str],
        expires_at: Optional[float] = None,
    ) -> AuthContext:
        return self._register(
            self._api_keys,
            token,
            "token",
            subject,
            workspace_id,
            scopes,
            roles,
            expires_at,
        )

    def register_browser_session(
        self,
        session_id: str,
        *,
        subject: str,
        workspace_id: str,
        scopes: Iterable[str],
        roles: Iterable[str],
        expires_at: Optional[float] = None,
    ) -> AuthContext:
        return self._register(
            self._sessions,
            session_id,
            "browser-session",
            subject,
            workspace_id,
            scopes,
            roles,
            expires_at,
        )

    def revoke_api_key(self, token: str) -> bool:
        return self._mark(self._api_keys, token, "revoked")

    def disable_api_key(self, token: str) -> bool:
        return self._mark(self._api_keys, token, "disabled")

    def revoke_browser_session(self, session_id: str) -> bool:
        return self._mark(self._sessions, session_id, "revoked")

    def disable_browser_session(self, session_id: str) -> bool:
        return self._mark(self._sessions, session_id, "disabled")

    def validate_request(
        self,
        *,
        headers,
        cookies,
        workspace_id: str,
        required_scope: str = TASK_MONITOR_SCOPE,
        required_role: str = WORKSPACE_VIEWER_ROLE,
        path: str = "",
    ) -> AuthContext:
        credential_ref, records, client_type = self._extract_credential(
            headers,
            cookies,
            path,
        )
        return self._validate_record(
            records,
            credential_ref,
            workspace_id,
            required_scope,
            required_role,
            client_type,
            path,
        )

    def revalidate_task_monitor_poll(
        self,
        context: AuthContext,
        workspace_id: str,
    ) -> AuthContext:
        records = (
            self._api_keys
            if context.client_type == "token"
            else self._sessions
        )
        return self._validate_record(
            records,
            context.credential_ref,
            workspace_id,
            TASK_MONITOR_SCOPE,
            WORKSPACE_VIEWER_ROLE,
            context.client_type,
            "task-monitor-poll",
        )

    def audit_events(self) -> List[Dict[str, str]]:
        with self._lock:
            return [dict(event) for event in self._audit_events]

    def _register(
        self,
        records: Dict[str, _CredentialRecord],
        secret: str,
        client_type: str,
        subject: str,
        workspace_id: str,
        scopes: Iterable[str],
        roles: Iterable[str],
        expires_at: Optional[float],
    ) -> AuthContext:
        if not secret or not secret.strip():
            raise ValueError("credential secret is required")
        credential_ref = self._fingerprint(secret)
        context = AuthContext(
            subject=subject,
            workspace_id=workspace_id,
            scopes=frozenset(scopes),
            roles=frozenset(roles),
            client_type=client_type,
            credential_ref=credential_ref,
        )
        with self._lock:
            records[credential_ref] = _CredentialRecord(
                context,
                False,
                False,
                expires_at,
            )
        return context

    def _mark(
        self,
        records: Dict[str, _CredentialRecord],
        secret: str,
        state: str,
    ) -> bool:
        credential_ref = self._fingerprint(secret)
        with self._lock:
            record = records.get(credential_ref)
            if not record:
                return False
            if state == "revoked":
                record.revoked = True
            elif state == "disabled":
                record.disabled = True
            return True

    def _extract_credential(self, headers, cookies, path: str):
        header = headers.get("authorization", "")
        if header:
            prefix = "Bearer "
            if (
                not header.startswith(prefix)
                or not header[len(prefix):].strip()
            ):
                self._deny(401, "malformed", None, "token", path)
            return (
                self._fingerprint(header[len(prefix):].strip()),
                self._api_keys,
                "token",
            )

        session_id = cookies.get("ao_session")
        if session_id:
            return (
                self._fingerprint(session_id),
                self._sessions,
                "browser-session",
            )

        self._deny(401, "anonymous", None, "anonymous", path)

    def _validate_record(
        self,
        records: Dict[str, _CredentialRecord],
        credential_ref: str,
        workspace_id: str,
        required_scope: str,
        required_role: str,
        client_type: str,
        path: str,
    ) -> AuthContext:
        with self._lock:
            record = records.get(credential_ref)
            if not record:
                self._deny(401, "unknown", None, client_type, path)
            context = record.context
            if record.revoked:
                self._deny(401, "revoked", context, client_type, path)
            if record.disabled:
                self._deny(403, "disabled", context, client_type, path)
            if (
                record.expires_at is not None
                and record.expires_at <= time.time()
            ):
                self._deny(401, "expired", context, client_type, path)
            if context.workspace_id != workspace_id:
                self._deny(403, "wrong_workspace", context, client_type, path)
            if (
                required_scope not in context.scopes
                and "*" not in context.scopes
            ):
                self._deny(
                    403,
                    "insufficient_scope",
                    context,
                    client_type,
                    path,
                )
            if not self._has_role(context.roles, required_role):
                self._deny(
                    403,
                    "insufficient_role",
                    context,
                    client_type,
                    path,
                )
            self._audit("allow", "authorized", context, client_type, path)
            return context

    def _deny(
        self,
        status_code: int,
        reason: str,
        context: Optional[AuthContext],
        client_type: str,
        path: str,
    ):
        self._audit("deny", reason, context, client_type, path)
        raise AuthError(status_code, reason)

    def _audit(
        self,
        event: str,
        reason: str,
        context: Optional[AuthContext],
        client_type: str,
        path: str,
    ) -> None:
        with self._lock:
            self._audit_events.append(
                {
                    "event": event,
                    "reason": reason,
                    "client_type": client_type,
                    "subject": context.subject if context else "",
                    "workspace_id": context.workspace_id if context else "",
                    "path": path,
                }
            )

    @staticmethod
    def _has_role(roles: FrozenSet[str], required_role: str) -> bool:
        return (
            required_role in roles
            or WORKSPACE_ADMIN_ROLE in roles
            or "admin" in roles
        )

    @staticmethod
    def _fingerprint(secret: str) -> str:
        return hashlib.sha256(f"ao-auth:{secret}".encode("utf-8")).hexdigest()


auth_service = AuthService()
