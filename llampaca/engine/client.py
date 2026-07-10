"""
OpenAI-compatible client for the local llama-server instance (asynchronous).

Two levels of API:

- chat_stream(): simple text-only streaming, used for plain (non-agentic)
  chat. Swallows errors into the text stream for a friendly terminal UX.

- chat_stream_events(): structured streaming used by the agent loop.
  Supports the OpenAI "tools" parameter and reassembles tool calls that
  arrive split across many stream chunks. Yields events and lets errors
  propagate, so the caller (the Agent) can decide how to react — e.g.
  retry without tools if the model's chat template doesn't support them.
"""

from typing import Any, Dict, AsyncGenerator, List, Optional, Tuple
# pyrefly: ignore [missing-import]
from openai import AsyncOpenAI


class LlamaClient:
    def __init__(self, port: int = 8080):
        """
        Initialize the OpenAI-compatible client pointing to the local llama-server.
        """
        self.client = AsyncOpenAI(
            base_url=f"http://127.0.0.1:{port}/v1",
            api_key="llampaca-local-key"  # No key required, using placeholder
        )

    async def chat_stream(self, messages: List[Dict[str, str]], model: str = "local-model") -> AsyncGenerator[str, None]:
        """
        Stream the chat completion text chunks from llama-server (text only,
        no tool support). Errors are yielded inline as text.
        """
        try:
            response = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True
            )
            async for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            yield f"\n[Client Error: {e}]"

    async def chat_stream_events(
        self,
        messages: List[Dict[str, Any]],
        model: str = "local-model",
        tools: Optional[List[dict]] = None,
    ) -> AsyncGenerator[Tuple[str, Any], None]:
        """
        Stream a chat completion as structured events, with tool support.

        Yields:
            ("text", str) — a chunk of assistant text, as soon as it arrives
                (so the UI can render it live);
            ("message", dict) — exactly once, at the end: the fully assembled
                assistant message in OpenAI format, including "tool_calls" if
                the model requested any. The caller appends this to the
                conversation history and inspects it for tool calls.

        Note on tool call reassembly: in streaming mode the server sends tool
        calls in fragments — the function name in one chunk, the JSON
        arguments spread over many following chunks — each tagged with an
        "index" identifying which call it belongs to. We accumulate the
        fragments per index and rebuild the complete calls at the end.
        """
        request_kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        # Only include the tools parameter when there are tools: sending an
        # empty list can confuse some server versions.
        if tools:
            request_kwargs["tools"] = tools

        stream = await self.client.chat.completions.create(**request_kwargs)

        content = ""
        # index -> partial tool call being reassembled from stream fragments
        pending_tool_calls: Dict[int, Dict[str, str]] = {}

        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue

            # Plain assistant text: forward immediately for live rendering
            if delta.content:
                content += delta.content
                yield ("text", delta.content)

            # Tool call fragments: accumulate by index
            if delta.tool_calls:
                for fragment in delta.tool_calls:
                    entry = pending_tool_calls.setdefault(
                        fragment.index, {"id": "", "name": "", "arguments": ""}
                    )
                    if fragment.id:
                        entry["id"] = fragment.id
                    if fragment.function:
                        if fragment.function.name:
                            entry["name"] = fragment.function.name
                        if fragment.function.arguments:
                            # Arguments arrive as JSON text spread over chunks
                            entry["arguments"] += fragment.function.arguments

        # Assemble the final OpenAI-format assistant message
        message: Dict[str, Any] = {"role": "assistant", "content": content or None}
        if pending_tool_calls:
            message["tool_calls"] = [
                {
                    # Some servers omit the id in streaming; synthesize one,
                    # since the tool result message must reference it
                    "id": entry["id"] or f"call_{index}",
                    "type": "function",
                    "function": {
                        "name": entry["name"],
                        "arguments": entry["arguments"] or "{}",
                    },
                }
                for index, entry in sorted(pending_tool_calls.items())
            ]

        yield ("message", message)

    async def get_models(self) -> List[str]:
        """
        Query the active llama-server for available models.
        """
        try:
            models_data = await self.client.models.list()
            return [m.id for m in models_data.data]
        except Exception:
            return []
