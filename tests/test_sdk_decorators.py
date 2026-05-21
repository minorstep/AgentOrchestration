import asyncio

import pytest

from src.sdk.decorators import on_event


class TestOnEvent:
    def test_on_event_sets_event_handler_metadata(self):
        async def handle_user_created():
            return "handled"

        wrapped = on_event("user.created")(handle_user_created)

        assert wrapped.__event_handler__ == "user.created"

    def test_on_event_normalizes_surrounding_whitespace(self):
        async def handle_user_created():
            return "handled"

        wrapped = on_event("  user.created  ")(handle_user_created)

        assert wrapped.__event_handler__ == "user.created"

    def test_on_event_preserves_wrapped_handler_behaviour(self):
        seen_payloads = []

        @on_event("agent.started")
        async def handle_agent_started(payload):
            seen_payloads.append(payload)
            return "handled"

        payload = {"agent_id": "agent-1"}

        assert handle_agent_started.__event_handler__ == "agent.started"
        assert asyncio.run(handle_agent_started(payload)) == "handled"
        assert seen_payloads == [payload]

    @pytest.mark.parametrize("event_type", ["", "   ", "\t\n"])
    def test_on_event_rejects_blank_event_type(self, event_type):
        with pytest.raises(
            ValueError,
            match="event_type must be a non-empty string",
        ):
            on_event(event_type)

    def test_on_event_rejects_non_string_event_type(self):
        with pytest.raises(
            ValueError,
            match="event_type must be a non-empty string",
        ):
            on_event(None)
