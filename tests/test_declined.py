import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock

from llampaca.agent.loop import Agent, DECLINED_MARKER
from llampaca.engine.client import LlamaClient
from llampaca.tools.registry import ToolRegistry


class TestAgentDeclined(unittest.IsolatedAsyncioTestCase):
    async def test_declined_tool_stops_agent_loop(self):
        registry = ToolRegistry()
        def dangerous_tool():
            return "done"

        registry.register(dangerous_tool, requires_confirmation=True)

        confirm_mock = AsyncMock(return_value=False)
        client_mock = MagicMock(spec=LlamaClient)
        client_mock.get_chat_template.return_value = ""

        # Native tool call message emitted by chat_stream_events
        tool_call_msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "dangerous_tool",
                        "arguments": "{}"
                    }
                }
            ]
        }

        async def fake_events(messages, model="local-model", tools=None):
            yield ("tool_name", "dangerous_tool")
            yield ("message", tool_call_msg)

        client_mock.chat_stream_events = fake_events

        agent = Agent(client=client_mock, registry=registry, confirm=confirm_mock)
        agent.native_tools = True
        agent.tools_enabled = True
        agent._tools_confirmed_working = True

        events = []
        async for ev_type, ev_data in agent.send("do something dangerous"):
            events.append((ev_type, ev_data))

        # Verify confirm was called
        confirm_mock.assert_called_once()

        # Verify events include tool_call, tool_result with DECLINED, and text cancellation
        event_types = [e[0] for e in events]
        self.assertIn("tool_call", event_types)
        self.assertIn("tool_result", event_types)
        self.assertIn("text", event_types)

        # Verify text cancellation message was emitted
        text_events = [e[1] for e in events if e[0] == "text"]
        self.assertTrue(any("Operazione annullata" in t for t in text_events))

        # Verify tool_result contains DECLINED_MARKER
        tool_result_events = [e[1] for e in events if e[0] == "tool_result"]
        self.assertTrue(tool_result_events[0]["result"].startswith(DECLINED_MARKER))


if __name__ == "__main__":
    unittest.main()
