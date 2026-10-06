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

    def do_POST(self):  # noqa: N802 (http.server naming)
        length = int(self.headers["Content-Length"])
        StubOllama.received.append((self.path, json.loads(self.rfile.read(length))))
        body = json.dumps(ANSWER).encode()
        self.send_response(200)
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
