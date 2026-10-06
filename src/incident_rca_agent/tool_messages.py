"""How tool calls are written down for Ollama, in one place.

If a live smoke test shows that the model expects another shape, this is the only file to change:

- the request lists the tools in the function format: {"type": "function", "function": {...}};
- after the model asks for tools, the conversation gets the assistant's message with its tool calls,
  then one "tool" message per call with the result text.

The final structured step never goes through here: it is a separate call with a schema and no tools.
"""

from __future__ import annotations

from typing import Any

from .llm import Reply


def tool_spec(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}


def assistant_message(reply: Reply) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": reply.text,
        "tool_calls": [{"function": {"name": call.name, "arguments": call.arguments}} for call in reply.tool_calls],
    }


def tool_result_message(name: str, content: str) -> dict[str, Any]:
    return {"role": "tool", "tool_name": name, "content": content}
