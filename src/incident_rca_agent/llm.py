"""Model connector: talks to a local Ollama server, and records or replays every call.

Three modes:

- replay (default): answers come from runs/<model-tag>/calls.jsonl, so no model is needed.
- live: every call goes to the model.
- record: like live, and every call is appended to calls.jsonl.

A call is identified by a fingerprint of exactly what was sent (model, messages, schema, tools,
options). If the code or the data changes what is asked, the fingerprint changes and replay
stops with ReplayMiss instead of quietly reusing an old answer.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "ministral-3:8b"
DEFAULT_HOST = "http://127.0.0.1:11434"
MODES = ("replay", "live", "record")

Transport = Callable[[dict[str, Any]], dict[str, Any]]


class ReplayMiss(RuntimeError):
    """The stored run has no answer for this exact request."""


class InvalidOutput(RuntimeError):
    """The model's answer did not match the schema, even after one retry."""


class ModelUnavailable(RuntimeError):
    """The Ollama server could not be reached."""


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    data: Any = None
    seconds: float = 0.0
    prompt_tokens: int = 0
    output_tokens: int = 0


def fingerprint(request: dict[str, Any]) -> str:
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_dir_name(model: str) -> str:
    return model.replace(":", "-").replace("/", "-")


def ollama_transport(host: str | None = None) -> Transport:
    """Send a request to Ollama's /api/chat with the standard library and return a plain dict."""
    base = (host or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST).rstrip("/")
    if not base.startswith(("http://", "https://")):
        base = f"http://{base}"

    def send(request: dict[str, Any]) -> dict[str, Any]:
        body = {
            "model": request["model"],
            "messages": request["messages"],
            "stream": False,
            "options": request["options"],
        }
        for key in ("format", "tools"):
            if key in request:
                body[key] = request[key]
        http_request = urllib.request.Request(
            f"{base}/api/chat", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(http_request, timeout=900) as response:
                data = json.load(response)
        except urllib.error.HTTPError as error:
            raise ModelUnavailable(f"Ollama at {base} answered {error.code}: {error.read().decode()[:300]}") from error
        except (urllib.error.URLError, ConnectionError) as error:
            raise ModelUnavailable(
                f"Ollama is not reachable at {base} ({error}). Start Ollama, or use the stored run (replay mode)."
            ) from error
        message = data.get("message") or {}
        return {
            "content": message.get("content") or "",
            "tool_calls": [
                {"name": call["function"]["name"], "arguments": call["function"].get("arguments") or {}}
                for call in message.get("tool_calls") or []
            ],
            "prompt_tokens": data.get("prompt_eval_count", 0),
            "output_tokens": data.get("eval_count", 0),
            "done_reason": data.get("done_reason"),
        }

    return send


def _get_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)


def ollama_info(model: str, host: str | None = None) -> dict[str, str]:
    """Ollama version and model digest, for the record of a run. Empty when Ollama is not running."""
    base = (host or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST).rstrip("/")
    try:
        version = _get_json(f"{base}/api/version").get("version", "")
        models = _get_json(f"{base}/api/tags").get("models", [])
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return {}
    digest = next((entry.get("digest", "") for entry in models if model in (entry.get("model"), entry.get("name"))), "")
    return {"ollama_version": version, "model_digest": digest}


def machine_description() -> str:
    """For example "Apple M4, 16 GB". Falls back to the platform name."""
    import platform
    import subprocess

    try:
        chip = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, check=True
        ).stdout.strip()
        memory = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True).stdout)
        return f"{chip}, {round(memory / 2**30)} GB"
    except (OSError, subprocess.CalledProcessError, ValueError):
        return f"{platform.system()} {platform.machine()}".strip() or "unknown machine"


class LLM:
    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        mode: str = "replay",
        runs_root: str | Path = "runs",
        num_ctx: int = 8192,
        seed: int = 7,
        transport: Transport | None = None,
    ):
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}, use one of {MODES}")
        self.model = model
        self.mode = mode
        self.options = {"temperature": 0, "seed": seed, "num_ctx": num_ctx}
        self.run_dir = Path(runs_root) / run_dir_name(model)
        self.calls_path = self.run_dir / "calls.jsonl"
        self._transport = transport
        self._stored: dict[str, dict[str, Any]] | None = None

    def start_recording(self) -> None:
        """Begin a fresh run: the previous calls.jsonl is replaced."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.calls_path.write_text("", encoding="utf-8")

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        schema: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        label: str = "",
    ) -> Reply:
        request: dict[str, Any] = {"model": self.model, "messages": messages, "options": self.options}
        if schema is not None:
            request["format"] = schema
        if tools:
            request["tools"] = tools
        key = fingerprint(request)

        if self.mode == "replay":
            stored = self._lookup(key, label)
            response, seconds = stored["response"], stored["seconds"]
        else:
            started = time.perf_counter()
            response = self._send(request)
            seconds = round(time.perf_counter() - started, 3)
            if self.mode == "record":
                self._append({"fingerprint": key, "label": label, "seconds": seconds, "response": response})

        return Reply(
            text=response.get("content", ""),
            tool_calls=[ToolCall(call["name"], call["arguments"]) for call in response.get("tool_calls", [])],
            seconds=seconds,
            prompt_tokens=response.get("prompt_tokens", 0),
            output_tokens=response.get("output_tokens", 0),
        )

    def structured(
        self,
        messages: list[dict[str, Any]],
        *,
        schema: dict[str, Any],
        validate: Callable[[Any], Any],
        label: str = "",
    ) -> Reply:
        """Ask for JSON that matches a schema. One retry with the error; then InvalidOutput."""
        first = self.chat(messages, schema=schema, label=label)
        try:
            first.data = validate(json.loads(first.text))
            return first
        except ValueError as error:
            problem = str(error)[:400]
        retry_messages = [
            *messages,
            {"role": "assistant", "content": first.text},
            {
                "role": "user",
                "content": f"That answer could not be used: {problem}. "
                "Reply again with JSON only, matching the schema exactly.",
            },
        ]
        second = self.chat(retry_messages, schema=schema, label=f"{label} (retry)")
        second.seconds = round(first.seconds + second.seconds, 3)
        try:
            second.data = validate(json.loads(second.text))
            return second
        except ValueError as error:
            raise InvalidOutput(f"{label or 'request'}: answer still invalid after a retry: {error}") from error

    def _send(self, request: dict[str, Any]) -> dict[str, Any]:
        if self._transport is None:
            self._transport = ollama_transport()
        return self._transport(request)

    def _append(self, entry: dict[str, Any]) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with self.calls_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")

    def _lookup(self, key: str, label: str) -> dict[str, Any]:
        if self._stored is None:
            if not self.calls_path.exists():
                raise ReplayMiss(
                    f"No stored run at {self.calls_path}. Record one with `make record` (needs Ollama), "
                    "or run live with `make live`."
                )
            self._stored = {}
            for line in self.calls_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = json.loads(line)
                    self._stored[entry["fingerprint"]] = entry
        if key not in self._stored:
            raise ReplayMiss(
                f"No stored answer for {label or 'this request'} ({key[:12]}) in {self.calls_path}. "
                "The code or the data changed since the run was recorded; record a new run with `make record`."
            )
        return self._stored[key]
