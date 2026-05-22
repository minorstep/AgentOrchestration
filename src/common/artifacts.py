"""Artifact metadata, lifecycle sync and retrieval reporting helpers."""

import time
from dataclasses import dataclass, field
from enum import Enum
from threading import RLock
from typing import Any, Callable, Dict, List, Optional, Protocol, Union


class StorageClass(str, Enum):
    STANDARD = "standard"
    INFREQUENT_ACCESS = "infrequent_access"
    ARCHIVE = "archive"
    DEEP_ARCHIVE = "deep_archive"

    @classmethod
    def parse(cls, value: Union["StorageClass", str]) -> "StorageClass":
        if isinstance(value, cls):
            return value
        aliases = {
            "standard": cls.STANDARD,
            "standard_ia": cls.INFREQUENT_ACCESS,
            "infrequent_access": cls.INFREQUENT_ACCESS,
            "infrequent access": cls.INFREQUENT_ACCESS,
            "ia": cls.INFREQUENT_ACCESS,
            "archive": cls.ARCHIVE,
            "glacier": cls.ARCHIVE,
            "deep_archive": cls.DEEP_ARCHIVE,
            "deep archive": cls.DEEP_ARCHIVE,
            "deep-archive": cls.DEEP_ARCHIVE,
        }
        normalised = value.strip().lower().replace("-", "_")
        if normalised not in aliases:
            raise ValueError(f"Unknown storage class: {value}")
        return aliases[normalised]

    @property
    def retrieval_latency(self) -> str:
        return {
            StorageClass.STANDARD: "immediate",
            StorageClass.INFREQUENT_ACCESS: "slower than standard",
            StorageClass.ARCHIVE: "minutes to hours",
            StorageClass.DEEP_ARCHIVE: "hours",
        }[self]

    @property
    def is_slower_than_standard(self) -> bool:
        return self is not StorageClass.STANDARD


@dataclass
class StorageClassTransition:
    from_storage_class: StorageClass
    to_storage_class: StorageClass
    transitioned_at: float
    source: str = "lifecycle_sync"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from_storage_class": self.from_storage_class.value,
            "to_storage_class": self.to_storage_class.value,
            "transitioned_at": self.transitioned_at,
            "source": self.source,
        }


@dataclass
class ArtifactMetadata:
    artifact_id: str
    name: str = ""
    storage_class: StorageClass = StorageClass.STANDARD
    size_bytes: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_storage_transition_at: Optional[float] = None
    storage_class_transitions: List[StorageClassTransition] = field(
        default_factory=list
    )

    def record_storage_class(
        self,
        storage_class: Union[StorageClass, str],
        transitioned_at: Optional[float] = None,
        source: str = "lifecycle_sync",
    ) -> bool:
        new_class = StorageClass.parse(storage_class)
        observed_at = transitioned_at if transitioned_at is not None else time.time()
        if new_class == self.storage_class:
            self.updated_at = observed_at
            return False

        self.storage_class_transitions.append(
            StorageClassTransition(
                from_storage_class=self.storage_class,
                to_storage_class=new_class,
                transitioned_at=observed_at,
                source=source,
            )
        )
        self.storage_class = new_class
        self.last_storage_transition_at = observed_at
        self.updated_at = observed_at
        return True

    @property
    def retrieval_warning(self) -> Optional[str]:
        if not self.storage_class.is_slower_than_standard:
            return None
        label = self.storage_class.value.replace("_", " ")
        return (
            f"Artifact {self.artifact_id} is in {label} storage. "
            f"Retrieval may be {self.storage_class.retrieval_latency} "
            "and may incur additional cost."
        )

    def to_report(self) -> Dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "name": self.name,
            "storage_class": self.storage_class.value,
            "last_known_storage_class": self.storage_class.value,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_storage_transition_at": self.last_storage_transition_at,
            "retrieval_warning": self.retrieval_warning,
            "storage_class_transitions": [
                transition.to_dict()
                for transition in self.storage_class_transitions
            ],
        }


class ArtifactMetadataStore:
    def __init__(self):
        self._lock = RLock()
        self._artifacts: Dict[str, ArtifactMetadata] = {}

    def upsert(
        self,
        artifact_id: str,
        name: str = "",
        storage_class: Union[StorageClass, str] = StorageClass.STANDARD,
        size_bytes: int = 0,
    ) -> ArtifactMetadata:
        parsed_class = StorageClass.parse(storage_class)
        with self._lock:
            artifact = self._artifacts.get(artifact_id)
            if artifact is None:
                artifact = ArtifactMetadata(
                    artifact_id=artifact_id,
                    name=name,
                    storage_class=parsed_class,
                    size_bytes=size_bytes,
                )
                self._artifacts[artifact_id] = artifact
                return artifact

            if name:
                artifact.name = name
            if size_bytes:
                artifact.size_bytes = size_bytes
            artifact.record_storage_class(parsed_class, source="metadata_upsert")
            return artifact

    def record_storage_class(
        self,
        artifact_id: str,
        storage_class: Union[StorageClass, str],
        transitioned_at: Optional[float] = None,
        source: str = "lifecycle_sync",
    ) -> bool:
        with self._lock:
            artifact = self._artifacts[artifact_id]
            return artifact.record_storage_class(
                storage_class,
                transitioned_at=transitioned_at,
                source=source,
            )

    def get(self, artifact_id: str) -> Optional[ArtifactMetadata]:
        with self._lock:
            return self._artifacts.get(artifact_id)

    def list(self) -> List[ArtifactMetadata]:
        with self._lock:
            return list(self._artifacts.values())

    def reports(self) -> List[Dict[str, Any]]:
        return [artifact.to_report() for artifact in self.list()]


class StorageClassProvider(Protocol):
    def get_storage_class(
        self,
        artifact: ArtifactMetadata,
    ) -> Optional[Union[StorageClass, str]]:
        ...


Provider = Union[
    StorageClassProvider,
    Callable[[ArtifactMetadata], Optional[Union[StorageClass, str]]],
]


class ArtifactLifecycleSync:
    def __init__(
        self,
        store: ArtifactMetadataStore,
        provider: Provider,
        interval_seconds: float = 3600,
        clock: Callable[[], float] = time.time,
    ):
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self.store = store
        self.provider = provider
        self.interval_seconds = interval_seconds
        self.clock = clock
        self.last_sync_at: Optional[float] = None

    def should_run(self) -> bool:
        if self.last_sync_at is None:
            return True
        return self.clock() - self.last_sync_at >= self.interval_seconds

    def run_if_due(self) -> Optional[Dict[str, Any]]:
        if not self.should_run():
            return None
        return self.sync_once()

    def sync_once(self) -> Dict[str, Any]:
        checked = 0
        transitions = 0
        errors: Dict[str, str] = {}

        for artifact in self.store.list():
            checked += 1
            try:
                current_class = self._read_storage_class(artifact)
                if current_class is None:
                    continue
                if self.store.record_storage_class(
                    artifact.artifact_id,
                    current_class,
                    transitioned_at=self.clock(),
                ):
                    transitions += 1
            except Exception as exc:
                errors[artifact.artifact_id] = str(exc)

        self.last_sync_at = self.clock()
        report = ArtifactReporter(self.store).summary()
        return {
            "artifacts_checked": checked,
            "transitions_recorded": transitions,
            "errors": errors,
            "retrieval_warnings": report["retrieval_warnings"],
        }

    def _read_storage_class(
        self,
        artifact: ArtifactMetadata,
    ) -> Optional[Union[StorageClass, str]]:
        if callable(self.provider):
            return self.provider(artifact)
        return self.provider.get_storage_class(artifact)


class ArtifactReporter:
    def __init__(self, store: ArtifactMetadataStore):
        self.store = store

    def summary(self) -> Dict[str, Any]:
        reports = self.store.reports()
        by_class: Dict[str, int] = {storage_class.value: 0 for storage_class in StorageClass}
        warnings = []

        for report in reports:
            by_class[report["storage_class"]] += 1
            if report["retrieval_warning"]:
                warnings.append(
                    {
                        "artifact_id": report["artifact_id"],
                        "storage_class": report["storage_class"],
                        "message": report["retrieval_warning"],
                    }
                )

        return {
            "total_artifacts": len(reports),
            "by_storage_class": by_class,
            "retrieval_warnings": warnings,
            "artifacts": reports,
        }

    def retrieval_report(self, artifact_id: str) -> Dict[str, Any]:
        artifact = self.store.get(artifact_id)
        if artifact is None:
            raise KeyError(f"Unknown artifact: {artifact_id}")
        report = artifact.to_report()
        report["should_warn_before_retrieval"] = (
            artifact.storage_class.is_slower_than_standard
        )
        return report

    def storage_class_report(
        self,
        storage_class: Union[StorageClass, str],
    ) -> List[Dict[str, Any]]:
        parsed_class = StorageClass.parse(storage_class)
        return [
            artifact.to_report()
            for artifact in self.store.list()
            if artifact.storage_class == parsed_class
        ]


class MappingStorageClassProvider:
    def __init__(
        self,
        storage_classes: Optional[Dict[str, Union[StorageClass, str]]] = None,
    ):
        self.storage_classes = storage_classes or {}

    def get_storage_class(
        self,
        artifact: ArtifactMetadata,
    ) -> Optional[Union[StorageClass, str]]:
        return self.storage_classes.get(artifact.artifact_id)

    def set_storage_class(
        self,
        artifact_id: str,
        storage_class: Union[StorageClass, str],
    ) -> None:
        self.storage_classes[artifact_id] = storage_class
