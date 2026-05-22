import pytest

from src.common.metrics import metrics
from src.common.plugin_registry import PluginRegistry


def first_handler():
    return "first"


def second_handler():
    return "second"


class TestPluginRegistry:
    def setup_method(self):
        self.registry = PluginRegistry()

    def test_registers_and_resolves_plugin_capability(self):
        assert self.registry.register_plugin(
            "renderer-plugin",
            {"image.render": first_handler},
            tenant_id="tenant-a",
            version="1.0.0",
        )

        resolution = self.registry.resolve_capability("image.render")
        assert resolution["plugin_id"] == "renderer-plugin"
        assert resolution["handler"] is first_handler

        public_capabilities = self.registry.list_capabilities()
        assert public_capabilities["image.render"]["plugin_id"] == (
            "renderer-plugin"
        )
        assert "handler" not in public_capabilities["image.render"]

    def test_rejects_duplicate_capability_without_overwriting(self):
        rejected_before = metrics.snapshot()["counters"].get(
            "plugin_registry.registration.rejected",
            0,
        )
        assert self.registry.register_plugin(
            "renderer-plugin",
            {"image.render": first_handler},
        )
        assert not self.registry.register_plugin(
            "competing-plugin",
            {"image.render": second_handler},
        )

        resolution = self.registry.resolve_capability("image.render")
        assert resolution["plugin_id"] == "renderer-plugin"
        assert resolution["handler"] is first_handler
        assert (
            metrics.snapshot()["counters"][
                "plugin_registry.registration.rejected"
            ]
            == rejected_before + 1
        )

        duplicate_event = self.registry.audit_log()[-1]
        assert duplicate_event["accepted"] is False
        assert duplicate_event["reason"] == "duplicate_capability"
        assert duplicate_event["plugin_id"] == "competing-plugin"
        assert "handler" not in duplicate_event

    def test_unregister_invalidates_capability_resolution_cache(self):
        assert self.registry.register_plugin(
            "renderer-plugin",
            {"image.render": first_handler},
        )
        assert (
            self.registry.resolve_capability("image.render")["handler"]
            is first_handler
        )

        assert self.registry.unregister_plugin("renderer-plugin")
        assert self.registry.resolve_capability("image.render") is None

        assert self.registry.register_plugin(
            "renderer-plugin-v2",
            {"image.render": second_handler},
        )
        assert (
            self.registry.resolve_capability("image.render")["handler"]
            is second_handler
        )

    def test_rejects_invalid_plugin_registration(self):
        with pytest.raises(ValueError, match="plugin id is required"):
            self.registry.register_plugin("", {"image.render": first_handler})

        with pytest.raises(ValueError, match="at least one capability"):
            self.registry.register_plugin("renderer-plugin", {})

        with pytest.raises(ValueError, match="capability name is required"):
            self.registry.register_plugin(
                "renderer-plugin",
                {"": first_handler},
            )

        with pytest.raises(
            ValueError,
            match="capability handler must be callable",
        ):
            self.registry.register_plugin(
                "renderer-plugin",
                {"image.render": object()},
            )
