"""Collaboration authentication and saved-view membership checks."""

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Mapping, Optional, Set


SAVED_VIEW_SHARE_PREFIX = "/api/v2/saved-views/"
SHARE_SUFFIX = "/share"
SHARE_ROLES = {"admin", "owner", "editor"}


@dataclass(frozen=True)
class PrincipalRecord:
    user_id: str
    scopes: Set[str] = field(default_factory=set)
    workspace_roles: Dict[str, str] = field(default_factory=dict)
    disabled: bool = False
    revoked: bool = False
    expires_at: Optional[float] = None


@dataclass(frozen=True)
class Principal:
    user_id: str
    credential_type: str
    scopes: Set[str]
    workspace_roles: Dict[str, str]
    workspace_id: str


@dataclass(frozen=True)
class AuthDecision:
    allowed: bool
    status_code: int
    code: str
    message: str
    principal: Optional[Principal] = None


@dataclass(frozen=True)
class SavedView:
    view_id: str
    workspace_id: str
    owner_id: str


class PrincipalStore:
    def __init__(self) -> None:
        self._tokens: Dict[str, PrincipalRecord] = {}
        self._sessions: Dict[str, PrincipalRecord] = {}

    def add_token(self, token: str, record: PrincipalRecord) -> None:
        self._tokens[token] = record

    def add_session(self, session_id: str, record: PrincipalRecord) -> None:
        self._sessions[session_id] = record

    def get_token(self, token: str) -> Optional[PrincipalRecord]:
        return self._tokens.get(token)

    def get_session(self, session_id: str) -> Optional[PrincipalRecord]:
        return self._sessions.get(session_id)


class SavedViewStore:
    def __init__(self) -> None:
        self._views: Dict[str, SavedView] = {}

    def add(self, view: SavedView) -> None:
        self._views[view.view_id] = view

    def get(self, view_id: str) -> Optional[SavedView]:
        return self._views.get(view_id)


class CollaborationAuthService:
    """Central auth service for collaboration-protected saved-view actions."""

    def __init__(
        self,
        principal_store: Optional[PrincipalStore] = None,
        saved_view_store: Optional[SavedViewStore] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.principal_store = principal_store or PrincipalStore()
        self.saved_view_store = saved_view_store or SavedViewStore()
        self._clock = clock or time.time

    def authenticate_request(
        self,
        headers: Mapping[str, str],
        cookies: Mapping[str, str],
        path: str,
    ) -> AuthDecision:
        credential_type, credential = self._extract_credential(
            headers,
            cookies,
        )
        workspace_id = (
            headers.get("x-workspace-id")
            or headers.get("X-Workspace-Id")
            or "default"
        )

        if credential_type is None or credential is None:
            return self._deny(401, "AUTH_REQUIRED", "Authentication required")

        record = self._lookup_record(credential_type, credential)
        if record is None:
            return self._deny(
                401,
                "INVALID_CREDENTIAL",
                "Credential is invalid or malformed",
            )
        if record.disabled:
            return self._deny(403, "USER_DISABLED", "User is disabled")
        if record.revoked:
            return self._deny(
                403,
                "CREDENTIAL_REVOKED",
                "Credential has been revoked",
            )
        if (
            record.expires_at is not None
            and record.expires_at <= self._clock()
        ):
            return self._deny(
                401,
                "CREDENTIAL_EXPIRED",
                "Credential has expired",
            )

        required_scopes = self._required_scopes(path)
        if required_scopes.difference(record.scopes):
            return self._deny(
                403,
                "INSUFFICIENT_SCOPE",
                "Credential scope is insufficient",
            )

        if self._is_saved_view_share(path):
            role = (
                record.workspace_roles.get(workspace_id)
                or record.workspace_roles.get("*")
            )
            if role not in SHARE_ROLES:
                return self._deny(
                    403,
                    "INSUFFICIENT_ROLE",
                    "Workspace role is insufficient",
                )

            view_id = self._saved_view_id_from_path(path)
            view = self.saved_view_store.get(view_id)
            if view is None:
                return self._deny(
                    404,
                    "VIEW_NOT_FOUND",
                    "Saved view not found",
                )
            if view.workspace_id != workspace_id:
                return self._deny(
                    403,
                    "WORKSPACE_MISMATCH",
                    "Saved view does not belong to the authenticated "
                    "workspace",
                )

        return AuthDecision(
            allowed=True,
            status_code=200,
            code="OK",
            message="Authorized",
            principal=Principal(
                user_id=record.user_id,
                credential_type=credential_type,
                scopes=set(record.scopes),
                workspace_roles=dict(record.workspace_roles),
                workspace_id=workspace_id,
            ),
        )

    def _lookup_record(
        self,
        credential_type: str,
        credential: str,
    ) -> Optional[PrincipalRecord]:
        if credential_type == "token":
            return self.principal_store.get_token(credential)
        return self.principal_store.get_session(credential)

    def _extract_credential(
        self,
        headers: Mapping[str, str],
        cookies: Mapping[str, str],
    ) -> tuple[Optional[str], Optional[str]]:
        authorization = (
            headers.get("authorization")
            or headers.get("Authorization")
        )
        if authorization is not None:
            scheme, _, token = authorization.partition(" ")
            if scheme != "Bearer" or not token.strip():
                return "token", ""
            return "token", token.strip()

        session_id = (
            headers.get("x-session-id")
            or headers.get("X-Session-Id")
            or cookies.get("ao_session")
        )
        if session_id:
            return "session", session_id.strip()

        return None, None

    def _required_scopes(self, path: str) -> Set[str]:
        scopes = {"api:access"}
        if self._is_saved_view_share(path):
            scopes.add("saved-view:share")
        return scopes

    def _is_saved_view_share(self, path: str) -> bool:
        return (
            path.startswith(SAVED_VIEW_SHARE_PREFIX)
            and path.endswith(SHARE_SUFFIX)
        )

    def _saved_view_id_from_path(self, path: str) -> str:
        tail = path[len(SAVED_VIEW_SHARE_PREFIX):]
        return tail[: -len(SHARE_SUFFIX)]

    def _deny(self, status_code: int, code: str, message: str) -> AuthDecision:
        return AuthDecision(False, status_code, code, message)
