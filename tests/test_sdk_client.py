from src.sdk.client import OrchestratorClient


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return b'{"ok": true}'


class TestOrchestratorClientUrlAssembly:
    def test_trailing_base_slash_does_not_duplicate_api_separator(
        self,
        monkeypatch,
    ):
        captured = {}

        def fake_urlopen(req):
            captured["url"] = req.full_url
            return FakeResponse()

        monkeypatch.setattr("src.sdk.client.urlopen", fake_urlopen)

        client = OrchestratorClient(base_url="https://example.test/")

        assert client.list_agents() == {"ok": True}
        assert captured["url"] == "https://example.test/api/v2/agents"

    def test_repeated_trailing_base_slashes_are_normalized(self, monkeypatch):
        captured = {}

        def fake_urlopen(req):
            captured["url"] = req.full_url
            return FakeResponse()

        monkeypatch.setattr("src.sdk.client.urlopen", fake_urlopen)

        client = OrchestratorClient(base_url="https://example.test///")

        assert client.get_agent("agent-1") == {"ok": True}
        assert captured["url"] == "https://example.test/api/v2/agents/agent-1"

    def test_env_base_url_with_trailing_slash_is_normalized(self, monkeypatch):
        captured = {}

        def fake_urlopen(req):
            captured["url"] = req.full_url
            return FakeResponse()

        monkeypatch.setenv("AO_API_URL", "https://env.example.test/")
        monkeypatch.setattr("src.sdk.client.urlopen", fake_urlopen)

        client = OrchestratorClient()

        assert client.list_agents(status="running") == {"ok": True}
        assert captured["url"] == (
            "https://env.example.test/api/v2/agents?status=running"
        )

    def test_request_path_without_leading_slash_is_normalized(
        self,
        monkeypatch,
    ):
        captured = {}

        def fake_urlopen(req):
            captured["url"] = req.full_url
            return FakeResponse()

        monkeypatch.setattr("src.sdk.client.urlopen", fake_urlopen)

        client = OrchestratorClient(base_url="https://example.test/")

        assert client._request("GET", "agents") == {"ok": True}
        assert captured["url"] == "https://example.test/api/v2/agents"

    def test_build_url_preserves_query_strings(self):
        client = OrchestratorClient(base_url="https://example.test/")

        assert client._build_url("agents?status=running") == (
            "https://example.test/api/v2/agents?status=running"
        )
