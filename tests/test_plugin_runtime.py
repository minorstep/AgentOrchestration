import threading

from src.orchestrator.engine import OrchestrationEngine
from src.orchestrator.plugin_runtime import PluginRuntime, PluginRuntimeState


def plugin_callback(*args, **kwargs):
    return None


CALLBACK_REF = "tests.test_plugin_runtime:plugin_callback"


def manifest(plugin_id="safe-plugin", hooks=None):
    return {
        "id": plugin_id,
        "version": "1.0.0",
        "hooks": hooks
        or [
            {
                "id": "pre-execute",
                "event": "pre_execute",
                "callback": CALLBACK_REF,
            }
        ],
    }


class TestPluginRuntime:
    def test_invalid_manifest_is_rejected_before_hooks_are_registered(self):
        calls = []
        engine = OrchestrationEngine()
        runtime = PluginRuntime(engine, importer=calls.append)

        loaded = runtime.load_manifest(
            manifest(
                plugin_id="bad-plugin",
                hooks=[
                    {
                        "id": "bad-event",
                        "event": "before_everything",
                        "callback": CALLBACK_REF,
                    }
                ],
            )
        )

        record = runtime.get_record("bad-plugin")
        assert loaded is False
        assert record.state is PluginRuntimeState.REJECTED
        assert record.error == "unsupported_hook_event"
        assert engine._hooks["pre_execute"] == []
        assert calls == []
        assert runtime.audit_log[-1]["event"] == "rejected"

    def test_valid_manifest_registers_hooks_once_after_validation(self):
        engine = OrchestrationEngine()
        runtime = PluginRuntime(engine, importer=lambda _: plugin_callback)

        first_load = runtime.load_manifest(manifest())
        second_load = runtime.load_manifest(manifest())

        record = runtime.get_record("safe-plugin")
        assert first_load is True
        assert second_load is True
        assert record.state is PluginRuntimeState.LOADED
        assert record.hooks == ["pre-execute"]
        assert len(engine._hooks["pre_execute"]) == 1
        assert runtime.audit_log[-1]["event"] == "load_idempotent"

    def test_default_importer_resolves_module_callback(self):
        engine = OrchestrationEngine()
        runtime = PluginRuntime(engine)

        loaded = runtime.load_manifest(
            manifest(
                plugin_id="default-importer",
                hooks=[
                    {
                        "id": "math-ceil",
                        "event": "pre_execute",
                        "callback": "math:ceil",
                    }
                ],
            )
        )

        assert loaded is True
        assert engine._hooks["pre_execute"][0](1.2) == 2

    def test_import_failure_leaves_no_partial_hooks(self):
        def importer(reference):
            if reference.endswith(":missing_callback"):
                raise AttributeError(reference)
            return plugin_callback

        engine = OrchestrationEngine()
        runtime = PluginRuntime(engine, importer=importer)
        broken_manifest = manifest(
            plugin_id="broken-plugin",
            hooks=[
                {
                    "id": "first",
                    "event": "pre_execute",
                    "callback": CALLBACK_REF,
                },
                {
                    "id": "second",
                    "event": "post_execute",
                    "callback": "tests.test_plugin_runtime:missing_callback",
                },
            ],
        )

        first_load = runtime.load_manifest(broken_manifest)
        second_load = runtime.load_manifest(broken_manifest)

        record = runtime.get_record("broken-plugin")
        assert first_load is False
        assert second_load is False
        assert record.state is PluginRuntimeState.FAILED
        assert record.error == "hook_import_failed"
        assert record.attempts == 1
        assert engine._hooks["pre_execute"] == []
        assert engine._hooks["post_execute"] == []
        assert runtime.audit_log[-1]["event"] == "terminal_state_reused"

    def test_cancellation_before_registration_leaves_no_orphaned_hooks(self):
        holder = {}

        def importer(_):
            holder["runtime"].cancel("cancelled-plugin")
            return plugin_callback

        engine = OrchestrationEngine()
        runtime = PluginRuntime(engine, importer=importer)
        holder["runtime"] = runtime

        loaded = runtime.load_manifest(manifest(plugin_id="cancelled-plugin"))

        record = runtime.get_record("cancelled-plugin")
        assert loaded is False
        assert record.state is PluginRuntimeState.CANCELLED
        assert record.error == "cancelled"
        assert engine._hooks["pre_execute"] == []
        assert runtime.audit_log[-1]["event"] == "load_cancelled"

    def test_concurrent_loads_share_one_durable_outcome(self):
        importer_entered = threading.Event()
        release_import = threading.Event()
        imported = []

        def importer(reference):
            imported.append(reference)
            importer_entered.set()
            assert release_import.wait(2)
            return plugin_callback

        engine = OrchestrationEngine()
        runtime = PluginRuntime(engine, importer=importer)
        results = []
        first_loader = threading.Thread(
            target=lambda: results.append(runtime.load_manifest(manifest())),
        )

        first_loader.start()
        assert importer_entered.wait(2)
        concurrent_load = runtime.load_manifest(manifest())
        release_import.set()
        first_loader.join(2)

        record = runtime.get_record("safe-plugin")
        assert results == [True]
        assert concurrent_load is False
        assert record.state is PluginRuntimeState.LOADED
        assert record.attempts == 1
        assert imported == [CALLBACK_REF]
        assert len(engine._hooks["pre_execute"]) == 1
        assert any(
            entry["event"] == "load_in_progress"
            for entry in runtime.audit_log
        )
