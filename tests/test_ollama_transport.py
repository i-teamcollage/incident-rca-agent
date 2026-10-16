import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from incident_rca_agent.llm import ModelUnavailable, ollama_transport

ANSWER = {
    "model": "ministral-3:8b",
    "message": {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "lookup_order", "arguments": {"order_id": "NH-1042"}}}],
    },
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 211,
    "eval_count": 19,
}


class StubOllama(BaseHTTPRequestHandler):
    received = []
    status, answer = 200, ANSWER

    def do_POST(self):  # noqa: N802 (http.server naming)
        length = int(self.headers["Content-Length"])
        StubOllama.received.append((self.path, json.loads(self.rfile.read(length))))
        body = json.dumps(self.answer).encode()
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def serve():
    server = HTTPServer(("127.0.0.1", 0), StubOllama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_transport_posts_to_the_chat_endpoint_without_streaming():
    server = serve()
    try:
        send = ollama_transport(f"http://127.0.0.1:{server.server_port}")
        request = {
            "model": "ministral-3:8b",
            "messages": [{"role": "user", "content": "Where is order NH-1042?"}],
            "options": {"temperature": 0, "seed": 7, "num_ctx": 8192},
            "tools": [{"type": "function", "function": {"name": "lookup_order"}}],
        }
        reply = send(request)
    finally:
        server.shutdown()
    path, body = StubOllama.received[-1]
    assert path == "/api/chat"
    assert body["stream"] is False
    assert body["options"] == request["options"]
    assert body["tools"] == request["tools"]
    assert "format" not in body
    assert reply == {
        "content": "",
        "tool_calls": [{"name": "lookup_order", "arguments": {"order_id": "NH-1042"}}],
        "prompt_tokens": 211,
        "output_tokens": 19,
        "done_reason": "stop",
    }


def test_missing_server_gives_a_clear_message():
    send = ollama_transport("http://127.0.0.1:9")  # nothing listens on the discard port
    with pytest.raises(ModelUnavailable, match="Ollama"):
        send({"model": "m", "messages": [], "options": {}})


def answering(status, answer):
    class Stub(StubOllama):
        pass

    Stub.status, Stub.answer = status, answer
    server = HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_a_call_to_a_tool_that_does_not_exist_is_a_reply_without_tool_calls():
    # Ollama answers 500 when the model names a tool that was not offered (it once called a tool "DONE");
    # that is the model's answer, not a broken server, so one odd reply must not stop a run of hours.
    server = answering(500, {"error": "tool 'DONE' not found"})
    try:
        reply = ollama_transport(f"http://127.0.0.1:{server.server_port}")(
            {"model": "m", "messages": [], "options": {}, "tools": []}
        )
    finally:
        server.shutdown()
    assert reply == {
        "content": "",
        "tool_calls": [],
        "prompt_tokens": 0,
        "output_tokens": 0,
        "done_reason": "unknown tool: tool 'DONE' not found",
    }


def test_any_other_server_error_still_stops_with_a_clear_message():
    server = answering(500, {"error": "model runner has unexpectedly stopped"})
    try:
        with pytest.raises(ModelUnavailable, match="answered 500"):
            ollama_transport(f"http://127.0.0.1:{server.server_port}")({"model": "m", "messages": [], "options": {}})
    finally:
        server.shutdown()
