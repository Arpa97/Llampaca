"""
Tests for the live-streaming events added for perceived-latency UX:

- LlamaClient.chat_stream_events must surface the model's thinking
  (llama-server's "reasoning_content" delta extension) as ("reasoning", ...)
  events, WITHOUT letting it leak into the final assembled message (it is
  display-only and must never re-enter the conversation history).
- It must announce a tool call's name as ("tool_name", ...) as soon as the
  first fragment carrying the name arrives — long before the JSON arguments
  finish streaming — so the UI can show "calling X..." immediately.
- Agent.send must forward these as ("reasoning", ...) and
  ("tool_start", {"name": ...}) events.

The OpenAI SDK stream is faked with SimpleNamespace chunks: the client only
touches .choices[0].delta.{content, reasoning_content, tool_calls}, so a
plain attribute bag is a faithful stand-in.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# Add project root to sys.path so we can import llampaca
sys.path.insert(0, str(Path(__file__).parent.parent))

from llampaca.agent.loop import Agent
from llampaca.engine.client import LlamaClient


def _chunk(content=None, reasoning=None, tool_calls=None):
    """Build a fake OpenAI streaming chunk with the given delta fields."""
    delta = SimpleNamespace(
        content=content,
        reasoning_content=reasoning,
        tool_calls=tool_calls,
    )
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _tool_fragment(index, name=None, arguments=None, id=None):
    """Build a fake streamed tool-call fragment."""
    return SimpleNamespace(
        index=index,
        id=id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


async def _fake_stream(chunks):
    for chunk in chunks:
        yield chunk


def _client_with_chunks(chunks):
    """A LlamaClient whose OpenAI stream replays the given fake chunks."""
    client = LlamaClient(port=9999)
    client.client = MagicMock()
    client.client.chat.completions.create = AsyncMock(
        return_value=_fake_stream(chunks)
    )
    return client


class TestClientStreamEvents(unittest.IsolatedAsyncioTestCase):
    async def _collect(self, client):
        events = []
        async for event in client.chat_stream_events([{"role": "user", "content": "hi"}]):
            events.append(event)
        return events

    async def test_reasoning_is_streamed_but_kept_out_of_the_message(self):
        client = _client_with_chunks([
            _chunk(reasoning="thinking "),
            _chunk(reasoning="hard..."),
            _chunk(content="the answer"),
        ])
        events = await self._collect(client)

        self.assertEqual(
            [e for e in events if e[0] == "reasoning"],
            [("reasoning", "thinking "), ("reasoning", "hard...")],
        )
        final = events[-1]
        self.assertEqual(final[0], "message")
        # Display-only: reasoning must not contaminate the history message
        self.assertEqual(final[1]["content"], "the answer")

    async def test_tool_name_announced_before_arguments_complete(self):
        client = _client_with_chunks([
            _chunk(tool_calls=[_tool_fragment(0, name="read_file", id="call_a")]),
            _chunk(tool_calls=[_tool_fragment(0, arguments='{"path": ')]),
            _chunk(tool_calls=[_tool_fragment(0, arguments='"x.txt"}')]),
        ])
        events = await self._collect(client)

        # Announced exactly once, and before the final message event
        self.assertEqual(
            [e for e in events if e[0] == "tool_name"],
            [("tool_name", "read_file")],
        )
        self.assertEqual(events[0], ("tool_name", "read_file"))

        # The assembled call still carries the full reassembled arguments
        final_message = events[-1][1]
        self.assertEqual(
            final_message["tool_calls"][0]["function"],
            {"name": "read_file", "arguments": '{"path": "x.txt"}'},
        )


class TestAgentForwardsStreamEvents(unittest.IsolatedAsyncioTestCase):
    async def test_send_forwards_reasoning_and_tool_start(self):
        mock_client = MagicMock(spec=LlamaClient)
        mock_client.get_chat_template.return_value = ""

        async def fake_events(messages, model="local-model", tools=None):
            yield ("reasoning", "let me think")
            yield ("tool_name", "read_file")
            yield ("text", "done")
            yield ("message", {"role": "assistant", "content": "done"})

        mock_client.chat_stream_events = fake_events

        agent = Agent(client=mock_client, registry=None)
        events = []
        async for event in agent.send("hello"):
            events.append(event)

        self.assertIn(("reasoning", "let me think"), events)
        self.assertIn(("tool_start", {"name": "read_file"}), events)
        self.assertIn(("text", "done"), events)


if __name__ == "__main__":
    unittest.main()
