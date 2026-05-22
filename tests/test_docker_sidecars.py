from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "infra" / "docker-compose.yml"
SIDECAR_DOC = ROOT / "docs" / "sidecar-runtime.md"
REQUIRED_TMPFS_PATHS = {"/tmp", "/var/run/ao"}


def load_compose():
    with COMPOSE_FILE.open() as handle:
        return yaml.safe_load(handle)


def sidecar_services(compose):
    services = compose["services"]
    return {
        name: service
        for name, service in services.items()
        if name.endswith("-sidecar")
        or service.get("labels", {}).get("ao.sidecar") == "true"
    }


def tmpfs_paths(service):
    paths = set()
    for mount in service.get("tmpfs", []):
        paths.add(mount.split(":", 1)[0])
    return paths


def assert_valid_sidecar(service):
    assert service.get("read_only") is True
    assert REQUIRED_TMPFS_PATHS.issubset(tmpfs_paths(service))


def test_sidecar_services_use_read_only_root_filesystems():
    compose = load_compose()
    sidecars = sidecar_services(compose)

    assert sidecars
    for service in sidecars.values():
        assert_valid_sidecar(service)


def test_sidecar_writable_paths_are_documented():
    compose = load_compose()
    documented = SIDECAR_DOC.read_text()

    for service in sidecar_services(compose).values():
        for path in tmpfs_paths(service):
            assert path in documented


def test_sidecar_validation_rejects_missing_read_only_setting():
    compose = load_compose()
    service = next(iter(sidecar_services(compose).values())).copy()
    service.pop("read_only")

    try:
        assert_valid_sidecar(service)
    except AssertionError:
        return

    raise AssertionError("sidecar without read_only passed validation")
