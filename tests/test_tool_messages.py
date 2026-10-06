from incident_rca_agent.llm import Reply, ToolCall
from incident_rca_agent.tool_messages import assistant_message, tool_result_message, tool_spec


def test_tool_spec_uses_the_function_format():
    parameters = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
    assert tool_spec("search_runbooks", "Search the runbooks.", parameters) == {
        "type": "function",
        "function": {"name": "search_runbooks", "description": "Search the runbooks.", "parameters": parameters},
    }


def test_assistant_message_repeats_the_tool_calls_it_asked_for():
    reply = Reply(text="", tool_calls=[ToolCall("get_lineage", {"pipeline": "fct_orders", "direction": "upstream"})])
    assert assistant_message(reply) == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"function": {"name": "get_lineage", "arguments": {"pipeline": "fct_orders", "direction": "upstream"}}}
        ],
    }


def test_tool_result_names_the_tool():
    assert tool_result_message("list_changes", "[CHG-1] ...") == {
        "role": "tool",
        "tool_name": "list_changes",
        "content": "[CHG-1] ...",
    }
