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

import requests
# pyrefly: ignore [missing-import]
from openai import AsyncOpenAI


class LlamaClient:
    def __init__(self, port: int = 8080):
        """
        Initialize the OpenAI-compatible client pointing to the local llama-server.
        """
        self.port = port
        self.client = AsyncOpenAI(
            base_url=f"http://127.0.0.1:{port}/v1",
            api_key="llampaca-local-key"  # No key required, using placeholder
        )

    def get_chat_template(self) -> str:
        """
        Fetch the model's chat template from llama-server's /props endpoint
        (llama-server specific, not part of the OpenAI API).

        The agent inspects it to decide the tool-calling mode: a template that
        never mentions tools cannot render the OpenAI "tools" parameter, so
        tool definitions must be injected via the system prompt instead.

        Deliberately synchronous (blocking `requests`) even though the rest of
        this client is async: it is called once from Agent.__init__, which is a
        sync context, and a one-shot call at startup does not benefit from the
        event loop.
        """
        response = requests.get(f"http://127.0.0.1:{self.port}/props", timeout=5)
        response.raise_for_status()
        return response.json().get("chat_template", "") or ""

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
            ("reasoning", str) — a chunk of the model's *thinking* text.
                Reasoning models (Qwen3/3.5, DeepSeek-R1, ...) generate a
                hidden <think> block before answering and before every tool
                call; llama-server (--jinja) extracts it into the separate
                delta field "reasoning_content". Surfacing it matters for
                perceived latency: the thinking phase is often the bulk of
                the generated tokens, and without this event the user
                stares at a silent terminal the whole time. Reasoning is
                display-only: it is NOT part of the final "message" (it
                must not be fed back into the history — chat templates
                drop past reasoning, and re-sending it would break the
                server's prompt-prefix cache);
            ("tool_name", str) — a tool call was detected mid-stream: the
                function name, emitted as soon as it is known. The name
                arrives in the FIRST fragment of a call while its JSON
                arguments can take many seconds more to stream — this early
                signal lets the UI say "calling X..." immediately instead
                of staying silent until the end of the stream;
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
            # llama-server extension (passed via extra_body because it is not
            # part of the OpenAI API): reuse the KV cache of the common
            # prompt prefix between requests. The agent loop re-sends the
            # whole history on every tool round-trip, so without this every
            # iteration would re-process the entire prompt from scratch.
            # Recent llama-server builds default to true; passing it
            # explicitly protects against older binaries and makes the
            # dependency visible.
            "extra_body": {"cache_prompt": True},
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

            # Thinking text: forward for live (dimmed) rendering. Accessed
            # with getattr because "reasoning_content" is a llama-server
            # extension the OpenAI SDK's delta type doesn't declare — the
            # SDK still carries it as an extra attribute when present.
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                yield ("reasoning", reasoning)

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
                            # First time this call's name shows up: announce
                            # it right away (see "tool_name" in the
                            # docstring), then keep accumulating arguments.
                            if not entry["name"]:
                                yield ("tool_name", fragment.function.name)
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

    async def embed(
        self,
        texts: List[str],
        model: str = "local-embedder",
        batch_size: int = 32,
    ) -> List[List[float]]:
        """
        Embed a list of texts against a llama-server started with
        --embedding, via the OpenAI-compatible /v1/embeddings endpoint.

        Returns one vector per input text, in the same order as the input.

        Notes:
            - Batching: requests are sent in slices of batch_size texts.
              One text = one chunk (~500 tokens), so a batch stays well
              within the embedding server's context; slicing keeps any
              single HTTP request from carrying hundreds of chunks when a
              large document is indexed.
            - Order: the API tags each result with an "index" relative to
              its request; results are re-sorted by it defensively, then
              offset by the slice position, so the output order is
              guaranteed even if the server were to reply out of order.
            - Prefixes: query/document instruction prefixes are NOT applied
              here — the caller owns them (they are model-specific and
              asymmetric; see the embedding preset in config.py).
        """
        vectors: List[List[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            response = await self.client.embeddings.create(
                model=model,
                input=batch,
            )
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors.extend([item.embedding for item in ordered])
        return vectors

    async def get_models(self) -> List[str]:
        """
        Query the active llama-server for available models.
        """
        try:
            models_data = await self.client.models.list()
            return [m.id for m in models_data.data]
        except Exception:
            return []
