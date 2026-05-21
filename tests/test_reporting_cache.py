import pytest

from src.common.reporting_cache import (
    ReportAuthContext,
    ReportingAuthorizationError,
    ReportingCache,
)


class AccessStore:
    def __init__(self):
        self.allowed = set()

    def grant(self, user_id, workspace_id):
        self.allowed.add((user_id, workspace_id))

    def revoke(self, user_id, workspace_id):
        self.allowed.discard((user_id, workspace_id))

    def authorize(self, context):
        return (context.user_id, context.workspace_id) in self.allowed


def context(
    user_id="user-1",
    workspace_id="workspace-1",
    roles=("admin",),
    permissions=("reports:read",),
    access_version="1",
):
    return ReportAuthContext.create(
        user_id=user_id,
        workspace_id=workspace_id,
        roles=roles,
        permissions=permissions,
        access_version=access_version,
    )


class TestReportingCache:
    def test_cached_report_is_denied_after_workspace_removal(self):
        access = AccessStore()
        access.grant("user-1", "workspace-1")
        cache = ReportingCache()
        auth_context = context()

        first_result = cache.get_or_set(
            {"report": "revenue"},
            auth_context,
            lambda: {"rows": ["sensitive"]},
            access.authorize,
        )
        access.revoke("user-1", "workspace-1")

        with pytest.raises(ReportingAuthorizationError):
            cache.get_or_set(
                {"report": "revenue"},
                auth_context,
                lambda: {"rows": ["must-not-run"]},
                access.authorize,
            )

        assert first_result == {"rows": ["sensitive"]}
        assert cache.audit_log[-1]["event"] == "read_denied"

    def test_role_downgrade_does_not_reuse_admin_cache(self):
        access = AccessStore()
        access.grant("user-1", "workspace-1")
        cache = ReportingCache()
        produced = []

        admin_context = context(
            roles=("admin",),
            permissions=("reports:read", "reports:export"),
            access_version="1",
        )
        viewer_context = context(
            roles=("viewer",),
            permissions=("reports:read",),
            access_version="2",
        )

        admin_result = cache.get_or_set(
            {"report": "margin"},
            admin_context,
            lambda: produced.append("admin") or {"scope": "admin"},
            access.authorize,
        )
        viewer_result = cache.get_or_set(
            {"report": "margin"},
            viewer_context,
            lambda: produced.append("viewer") or {"scope": "viewer"},
            access.authorize,
        )

        assert admin_result == {"scope": "admin"}
        assert viewer_result == {"scope": "viewer"}
        assert produced == ["admin", "viewer"]

    def test_missing_permission_blocks_read_and_store(self):
        access = AccessStore()
        access.grant("user-1", "workspace-1")
        cache = ReportingCache()

        with pytest.raises(ReportingAuthorizationError):
            cache.get_or_set(
                {"report": "margin"},
                context(permissions=("runs:read",)),
                lambda: {"scope": "forbidden"},
                access.authorize,
            )

        assert cache.audit_log[-1]["event"] == "read_denied"

    def test_cache_key_includes_workspace_role_permission_and_version(self):
        cache = ReportingCache()
        query = {"report": "utilisation", "range": "month"}
        base = context()

        assert cache.cache_key(query, base) != cache.cache_key(
            query,
            context(workspace_id="workspace-2"),
        )
        assert cache.cache_key(query, base) != cache.cache_key(
            query,
            context(roles=("viewer",)),
        )
        assert cache.cache_key(query, base) != cache.cache_key(
            query,
            context(permissions=("reports:read", "reports:export")),
        )
        assert cache.cache_key(query, base) != cache.cache_key(
            query,
            context(access_version="2"),
        )

    def test_cached_payloads_are_defensive_copies(self):
        access = AccessStore()
        access.grant("user-1", "workspace-1")
        cache = ReportingCache()
        auth_context = context()

        first_result = cache.get_or_set(
            {"report": "clients"},
            auth_context,
            lambda: {"rows": [{"client": "A"}]},
            access.authorize,
        )
        first_result["rows"][0]["client"] = "mutated"
        second_result = cache.get_or_set(
            {"report": "clients"},
            auth_context,
            lambda: {"rows": [{"client": "must-not-run"}]},
            access.authorize,
        )

        assert second_result == {"rows": [{"client": "A"}]}
        assert cache.audit_log[-1]["event"] == "hit"

    def test_workspace_invalidation_removes_scoped_entries(self):
        access = AccessStore()
        access.grant("user-1", "workspace-1")
        access.grant("user-1", "workspace-2")
        cache = ReportingCache()

        cache.get_or_set(
            {"report": "a"},
            context(workspace_id="workspace-1"),
            lambda: {"workspace": "workspace-1"},
            access.authorize,
        )
        cache.get_or_set(
            {"report": "a"},
            context(workspace_id="workspace-2"),
            lambda: {"workspace": "workspace-2"},
            access.authorize,
        )

        assert cache.invalidate_workspace("workspace-1") == 1

        refreshed = cache.get_or_set(
            {"report": "a"},
            context(workspace_id="workspace-1"),
            lambda: {"workspace": "workspace-1-refreshed"},
            access.authorize,
        )
        cached = cache.get_or_set(
            {"report": "a"},
            context(workspace_id="workspace-2"),
            lambda: {"workspace": "must-not-run"},
            access.authorize,
        )

        assert refreshed == {"workspace": "workspace-1-refreshed"}
        assert cached == {"workspace": "workspace-2"}

    def test_access_is_rechecked_before_storing_produced_payload(self):
        access = AccessStore()
        access.grant("user-1", "workspace-1")
        cache = ReportingCache()
        produced = []

        def revoke_while_producing():
            produced.append("revoked-result")
            access.revoke("user-1", "workspace-1")
            return {"rows": ["must-not-cache"]}

        with pytest.raises(ReportingAuthorizationError):
            cache.get_or_set(
                {"report": "forecast"},
                context(),
                revoke_while_producing,
                access.authorize,
            )

        access.grant("user-1", "workspace-1")
        result = cache.get_or_set(
            {"report": "forecast"},
            context(),
            lambda: produced.append("fresh-result") or {"rows": ["fresh"]},
            access.authorize,
        )

        assert result == {"rows": ["fresh"]}
        assert produced == ["revoked-result", "fresh-result"]
        assert cache.audit_log[-2]["event"] == "store_denied"

    def test_ttl_expiry_and_entry_limit_force_fresh_results(self):
        access = AccessStore()
        access.grant("user-1", "workspace-1")
        now = [100.0]
        cache = ReportingCache(
            max_entries=1,
            ttl_seconds=5,
            clock=lambda: now[0],
        )
        produced = []

        cache.get_or_set(
            {"report": "old"},
            context(),
            lambda: produced.append("old-1") or {"rows": ["old-1"]},
            access.authorize,
        )
        now[0] = 106.0
        expired = cache.get_or_set(
            {"report": "old"},
            context(),
            lambda: produced.append("old-2") or {"rows": ["old-2"]},
            access.authorize,
        )
        cache.get_or_set(
            {"report": "new"},
            context(),
            lambda: produced.append("new") or {"rows": ["new"]},
            access.authorize,
        )
        evicted = cache.get_or_set(
            {"report": "old"},
            context(),
            lambda: produced.append("old-3") or {"rows": ["old-3"]},
            access.authorize,
        )

        assert expired == {"rows": ["old-2"]}
        assert evicted == {"rows": ["old-3"]}
        assert produced == ["old-1", "old-2", "new", "old-3"]
