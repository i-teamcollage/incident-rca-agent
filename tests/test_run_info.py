import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from incident_rca_agent.llm import machine_description, ollama_info

RESPONSES = {
    "/api/version": {"version": "0.12.6"},
    "/api/tags": {
        "models": [
            {"name": "gemma4:12b", "model": "gemma4:12b", "digest": "111"},
            {"name": "ministral-3:8b", "model": "ministral-3:8b", "digest": "a5c1f00d"},
        ]
    },
}


class StubOllama(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps(RESPONSES[self.path]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_ollama_info_reports_the_version_and_the_model_digest():
    server = HTTPServer(("127.0.0.1", 0), StubOllama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        info = ollama_info("ministral-3:8b", f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
    assert info == {"ollama_version": "0.12.6", "model_digest": "a5c1f00d"}


def test_ollama_info_without_a_server_is_empty():
    assert ollama_info("ministral-3:8b", "http://127.0.0.1:9") == {}


def test_machine_description_is_a_short_label():
    description = machine_description()
    assert description and len(description) < 80
