import pytest

from src.common.errors import AuthenticationError
from src.sdk.client import OrchestratorClient


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.payload


class TestOrchestratorClientAuthSetup:
    def test_missing_api_key_fails_before_any_request(self, monkeypatch):
        monkeypatch.delenv("AO_API_KEY", raising=False)

        def forbidden_urlopen(req):
            raise AssertionError(
                "request should not be created without an API key"
            )

        monkeypatch.setattr("src.sdk.client.urlopen", forbidden_urlopen)

        with pytest.raises(AuthenticationError) as exc_info:
            OrchestratorClient(base_url="https://example.test")

        message = str(exc_info.value)
        assert "AO_API_KEY is required" in message
        assert "Pass api_key or set AO_API_KEY" in message

    def test_blank_env_api_key_fails_before_headers(self, monkeypatch):
        monkeypatch.setenv("AO_API_KEY", "   ")

        with pytest.raises(AuthenticationError):
            OrchestratorClient(base_url="https://example.test")

    def test_blank_explicit_api_key_does_not_fall_back_to_env(
        self,
        monkeypatch,
    ):
        monkeypatch.setenv("AO_API_KEY", "env-secret")

        with pytest.raises(AuthenticationError):
            OrchestratorClient(
                base_url="https://example.test",
                api_key=" \t\n ",
            )

    def test_non_string_explicit_api_key_fails_clearly(self, monkeypatch):
        monkeypatch.delenv("AO_API_KEY", raising=False)

        with pytest.raises(AuthenticationError) as exc_info:
            OrchestratorClient(
                base_url="https://example.test",
                api_key=object(),
            )

        assert "must be a non-empty string" in str(exc_info.value)

    def test_explicit_api_key_is_trimmed_and_preferred_over_env(
        self,
        monkeypatch,
    ):
        captured = {}
        monkeypatch.setenv("AO_API_KEY", "env-secret")

        def fake_urlopen(req):
            captured["headers"] = dict(req.header_items())
            return FakeResponse(b'{"ok": true}')

        monkeypatch.setattr("src.sdk.client.urlopen", fake_urlopen)

        client = OrchestratorClient(
            base_url="https://example.test",
            api_key="  explicit-secret  ",
        )

        assert client.list_agents() == {"ok": True}
        assert captured["headers"]["Authorization"] == "Bearer explicit-secret"

    def test_env_api_key_is_trimmed_before_building_header(self, monkeypatch):
        captured = {}
        monkeypatch.setenv("AO_API_KEY", "  env-secret  ")

        def fake_urlopen(req):
            captured["headers"] = dict(req.header_items())
            return FakeResponse(b'{"agents": []}')

        monkeypatch.setattr("src.sdk.client.urlopen", fake_urlopen)

        client = OrchestratorClient(base_url="https://example.test")

        assert client.list_agents() == {"agents": []}
        assert captured["headers"]["Authorization"] == "Bearer env-secret"
