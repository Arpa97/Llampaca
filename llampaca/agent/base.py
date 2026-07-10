from typing import List, Dict, Callable
from llampaca.engine.client import LlamaClient

class LocalAgent:
    def __init__(self, port: int = 8080, system_prompt: str = None):
        self.client = LlamaClient(port=port)
        self.system_prompt = system_prompt or "You are Llampaca, a local AI agent assistant."
        self.tools: Dict[str, Callable] = {}
        
    def register_tool(self, name: str, func: Callable):
        """Register a local Python function as a tool (skill) for the agent."""
        self.tools[name] = func
        
    def get_tool_definitions(self) -> List[Dict]:
        """Return OpenAI-compatible tool definitions for registered tools."""
        # Future implementation will inspect function signatures and docstrings
        # to generate JSON schemas automatically.
        return []

    def run(self, prompt: str) -> str:
        """Run the agent loop (not fully implemented in this phase)."""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": prompt}
        ]
        
        # In a fully agentic workflow, we would:
        # 1. Query the model with tool definitions
        # 2. Check if the model wants to call a tool
        # 3. Execute the tool locally and feed the results back
        # 4. Repeat until final answer
        
        response = ""
        for chunk in self.client.chat_stream(messages):
            response += chunk
        return response
