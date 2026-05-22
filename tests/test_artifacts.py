import pytest

from src.common.artifacts import (
    ArtifactLifecycleSync,
    ArtifactMetadataStore,
    ArtifactReporter,
    MappingStorageClassProvider,
    StorageClass,
)


class ManualClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TestArtifactStorageClassTracking:
    def setup_method(self):
        self.clock = ManualClock()
        self.store = ArtifactMetadataStore()

    def test_metadata_records_storage_class_transitions(self):
        artifact = self.store.upsert("artifact-1", name="trace.json")

        changed = artifact.record_storage_class(
            StorageClass.ARCHIVE,
            transitioned_at=self.clock(),
            source="s3_lifecycle",
        )

        assert changed is True
        assert artifact.storage_class == StorageClass.ARCHIVE
        assert artifact.last_storage_transition_at == 1000.0
        assert artifact.storage_class_transitions[0].to_dict() == {
            "from_storage_class": "standard",
            "to_storage_class": "archive",
            "transitioned_at": 1000.0,
            "source": "s3_lifecycle",
        }

    def test_same_storage_class_observation_does_not_duplicate_history(self):
        artifact = self.store.upsert("artifact-1", storage_class="archive")

        changed = artifact.record_storage_class("archive", transitioned_at=1000.0)

        assert changed is False
        assert artifact.storage_class == StorageClass.ARCHIVE
        assert artifact.storage_class_transitions == []
        assert artifact.updated_at == 1000.0

    def test_report_shows_current_and_last_known_storage_class(self):
        artifact = self.store.upsert("artifact-1", name="model.bin")
        artifact.record_storage_class("glacier", transitioned_at=1000.0)

        report = ArtifactReporter(self.store).summary()

        assert report["artifacts"][0]["storage_class"] == "archive"
        assert report["artifacts"][0]["last_known_storage_class"] == "archive"
        assert report["artifacts"][0]["last_storage_transition_at"] == 1000.0
        assert report["by_storage_class"]["archive"] == 1

    def test_retrieval_report_warns_for_slow_storage(self):
        self.store.upsert("artifact-1", storage_class="deep_archive")

        report = ArtifactReporter(self.store).retrieval_report("artifact-1")

        assert report["should_warn_before_retrieval"] is True
        assert "additional cost" in report["retrieval_warning"]
        assert "deep archive" in report["retrieval_warning"]

    def test_retrieval_report_does_not_warn_for_standard_storage(self):
        self.store.upsert("artifact-1", storage_class="standard")

        report = ArtifactReporter(self.store).retrieval_report("artifact-1")

        assert report["should_warn_before_retrieval"] is False
        assert report["retrieval_warning"] is None

    def test_unknown_storage_class_is_rejected(self):
        with pytest.raises(ValueError):
            self.store.upsert("artifact-1", storage_class="frozen-mystery")


class TestArtifactLifecycleSync:
    def setup_method(self):
        self.clock = ManualClock()
        self.store = ArtifactMetadataStore()
        self.provider = MappingStorageClassProvider()
        self.sync = ArtifactLifecycleSync(
            self.store,
            self.provider,
            interval_seconds=60,
            clock=self.clock,
        )

    def test_scheduled_sync_records_provider_transitions(self):
        self.store.upsert("artifact-1", name="trace.json")
        self.provider.set_storage_class("artifact-1", "infrequent_access")

        result = self.sync.sync_once()

        artifact = self.store.get("artifact-1")
        assert artifact.storage_class == StorageClass.INFREQUENT_ACCESS
        assert artifact.last_storage_transition_at == 1000.0
        assert result["artifacts_checked"] == 1
        assert result["transitions_recorded"] == 1
        assert result["retrieval_warnings"][0]["storage_class"] == "infrequent_access"

    def test_sync_skips_artifacts_without_provider_update(self):
        artifact = self.store.upsert("artifact-1", storage_class="standard")

        result = self.sync.sync_once()

        assert artifact.storage_class == StorageClass.STANDARD
        assert result["artifacts_checked"] == 1
        assert result["transitions_recorded"] == 0
        assert result["retrieval_warnings"] == []

    def test_sync_captures_provider_errors_per_artifact(self):
        self.store.upsert("artifact-1")

        def failing_provider(artifact):
            raise RuntimeError("provider unavailable")

        sync = ArtifactLifecycleSync(
            self.store,
            failing_provider,
            interval_seconds=60,
            clock=self.clock,
        )

        result = sync.sync_once()

        assert result["transitions_recorded"] == 0
        assert result["errors"] == {"artifact-1": "provider unavailable"}

    def test_run_if_due_obeys_schedule(self):
        self.store.upsert("artifact-1")
        self.provider.set_storage_class("artifact-1", "archive")

        first = self.sync.run_if_due()
        second = self.sync.run_if_due()
        self.clock.advance(60)
        third = self.sync.run_if_due()

        assert first is not None
        assert second is None
        assert third is not None

    def test_storage_class_report_filters_artifacts(self):
        self.store.upsert("hot", storage_class="standard")
        self.store.upsert("cold", storage_class="archive")

        report = ArtifactReporter(self.store).storage_class_report("archive")

        assert [artifact["artifact_id"] for artifact in report] == ["cold"]
