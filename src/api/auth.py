"""Authentication helpers for API protected surfaces."""

import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional, Set

from fastapi import HTTPException, Request, status


@dataclass
class Principal:
    token: str
    scopes: Set[str] = field(default_factory=set)
    roles: Set[str] = field(default_factory=set)
    revoked: bool = False
    expires_at: Optional[float] = None

    @property
    def is_stale(self) -> bool:
        return self.expires_at is not None and self.expires_at <= time.time()


class AuthService:
    def __init__(self):
        self._principals: Dict[str, Principal] = {}

    def register_token(
        self,
        token: str,
        scopes: Iterable[str] = (),
        roles: Iterable[str] = (),
        revoked: bool = False,
        expires_at: Optional[float] = None,
    ) -> Principal:
        principal = Principal(
            token=token,
            scopes=set(scopes),
            roles=set(roles),
            revoked=revoked,
            expires_at=expires_at,
        )
        self._principals[token] = principal
        return principal

    def revoke_token(self, token: str) -> None:
        principal = self._principals.get(token)
        if principal is not None:
            principal.revoked = True

    def require_docs_access(self, request: Request) -> Principal:
        principal = self.authenticate_request(request)
        if "docs:read" not in principal.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing docs:read scope",
            )
        if "workspace:reader" not in principal.roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing workspace reader role",
            )
        return principal

    def authenticate_request(self, request: Request) -> Principal:
        token = self._token_from_request(request)
        if token is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )

        principal = self._principals.get(token)
        if principal is None or principal.revoked or principal.is_stale:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )
        return principal

    def _token_from_request(self, request: Request) -> Optional[str]:
        authorization = request.headers.get("Authorization", "")
        if authorization:
            scheme, _, token = authorization.partition(" ")
            if scheme != "Bearer" or not token.strip():
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Malformed authorization header",
                )
            return token.strip()

        session_token = request.cookies.get("ao_session")
        if session_token and session_token.strip():
            return session_token.strip()
        return None
