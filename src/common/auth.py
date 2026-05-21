"""Authentication helpers for service-to-service API calls."""

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, Iterable, Optional, Set


class JWTAuthError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))


def _json_b64url(data: Dict[str, Any]) -> str:
    return _b64url_encode(
        json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )


def create_service_token(
    claims: Dict[str, Any],
    secret: Optional[str] = None,
    audience: str = "agent-workers",
    ttl: int = 300,
    now: Optional[int] = None,
) -> str:
    issued_at = int(time.time() if now is None else now)
    payload = {
        "aud": audience,
        "iat": issued_at,
        "exp": issued_at + ttl,
        **claims,
    }
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = f"{_json_b64url(header)}.{_json_b64url(payload)}"
    signature = hmac.new(
        _resolve_secret(secret).encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{_b64url_encode(signature)}"


def validate_service_token(
    token: str,
    secret: Optional[str] = None,
    audience: str = "agent-workers",
    required_scope: Optional[str] = None,
    required_role: Optional[str] = None,
    revoked_tokens: Optional[Iterable[str]] = None,
    revoked_jti: Optional[Iterable[str]] = None,
    now: Optional[int] = None,
    clock_skew: int = 30,
) -> Dict[str, Any]:
    if not token or token in set(revoked_tokens or ()):
        raise JWTAuthError("revoked")

    parts = token.split(".")
    if len(parts) != 3:
        raise JWTAuthError("malformed")

    signing_input = f"{parts[0]}.{parts[1]}"
    expected_signature = hmac.new(
        _resolve_secret(secret).encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    try:
        supplied_signature = _b64url_decode(parts[2])
        header = json.loads(_b64url_decode(parts[0]))
        claims = json.loads(_b64url_decode(parts[1]))
    except Exception as exc:
        raise JWTAuthError("malformed") from exc

    if header.get("alg") != "HS256":
        raise JWTAuthError("malformed")
    if not hmac.compare_digest(expected_signature, supplied_signature):
        raise JWTAuthError("malformed")

    current_time = int(time.time() if now is None else now)
    if claims.get("jti") in set(revoked_jti or ()):
        raise JWTAuthError("revoked")
    if _timestamp(claims, "exp", 0) <= current_time - clock_skew:
        raise JWTAuthError("stale")
    if _timestamp(claims, "nbf", 0) > current_time + clock_skew:
        raise JWTAuthError("stale")
    if _timestamp(claims, "iat", 0) > current_time + clock_skew:
        raise JWTAuthError("stale")
    if claims.get("aud") != audience:
        raise JWTAuthError("wrong_audience")
    if (
        required_scope
        and required_scope not in _claim_set(claims, "scope", "scopes")
    ):
        raise JWTAuthError("insufficient_scope")
    if required_role and required_role not in _workspace_roles(claims):
        raise JWTAuthError("insufficient_workspace_role")

    return claims


def _resolve_secret(secret: Optional[str]) -> str:
    resolved = secret or os.getenv("AO_JWT_SECRET")
    if not resolved:
        raise JWTAuthError("missing_secret")
    return resolved


def _timestamp(claims: Dict[str, Any], key: str, default: int) -> int:
    try:
        return int(claims.get(key, default))
    except (TypeError, ValueError) as exc:
        raise JWTAuthError("malformed") from exc


def _claim_set(claims: Dict[str, Any], *keys: str) -> Set[str]:
    values: Set[str] = set()
    for key in keys:
        raw = claims.get(key)
        if isinstance(raw, str):
            values.update(item for item in raw.split() if item)
        elif isinstance(raw, list):
            values.update(str(item) for item in raw)
    return values


def _workspace_roles(claims: Dict[str, Any]) -> Set[str]:
    roles = _claim_set(claims, "workspace_role", "workspace_roles")
    workspace = claims.get("workspace")
    if isinstance(workspace, dict):
        roles.update(_claim_set(workspace, "role", "roles"))
    return roles
