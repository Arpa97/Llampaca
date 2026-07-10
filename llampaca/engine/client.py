from typing import Generator, List, Dict
from openai import OpenAI

class LlamaClient:
    def __init__(self, port: int = 8080):
        """
        Initialize the OpenAI-compatible client pointing to the local llama-server.
        """
        self.client = OpenAI(
            base_url=f"http://127.0.0.1:{port}/v1",
            api_key="llampaca-local-key"  # No key required, using placeholder
        )

    def chat_stream(self, messages: List[Dict[str, str]], model: str = "local-model") -> Generator[str, None, None]:
        """
        Stream the chat completion chunks from llama-server.
        """
        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True
            )
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            yield f"\n[Client Error: {e}]"
            
    def get_models(self) -> List[str]:
        """
        Query the active llama-server for available models.
        """
        try:
            models_data = self.client.models.list()
            return [m.id for m in models_data.data]
        except Exception:
            return []
