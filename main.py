#!/usr/bin/env python3
"""
Free Model Aggregator — OpenAI + Anthropic compatible proxy to OpenRouter free models.

Usage:
    OPENROUTER_API_KEY=sk-or-... python main.py [--port 8080] [--host 0.0.0.0]

Endpoints:
    POST /v1/chat/completions   — OpenAI-compatible
    POST /v1/messages           — Anthropic-compatible
    GET  /v1/models             — list free models
    GET  /health                — health check
"""

import http.server
import json
import os
import sys
import ssl
import threading
import urllib.request
import urllib.error
from urllib.parse import urlparse, parse_qs
from typing import Optional

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
FREE_MODELS_URL = f"{OPENROUTER_BASE}/models?free=true"

# ---------------------------------------------------------------------------
# Model catalog
# ---------------------------------------------------------------------------

_free_models: dict[str, dict] = {}  # id -> model info
_free_model_ids: list[str] = []
DEFAULT_FREE_MODEL = "google/gemini-2.0-flash-001"


def fetch_free_models(api_key: str) -> dict[str, dict]:
    """Fetch the free-model catalog from OpenRouter. Raises on failure."""
    req = urllib.request.Request(
        FREE_MODELS_URL,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode())
    models = {}
    for m in body.get("data", []):
        mid = m.get("id", "")
        if mid:
            models[mid] = m
    return models


def ensure_models_loaded(api_key: str):
    """Lazy-init the global model catalog (idempotent)."""
    global _free_models, _free_model_ids, DEFAULT_FREE_MODEL
    if _free_models:
        return
    _free_models = fetch_free_models(api_key)
    _free_model_ids = sorted(_free_models.keys())
    if DEFAULT_FREE_MODEL not in _free_models and _free_model_ids:
        # fallback to first free model
        DEFAULT_FREE_MODEL = _free_model_ids[0]


def pick_model(requested: Optional[str]) -> str:
    """Return a valid free model id, or raise ValueError."""
    if requested and requested in _free_models:
        return requested
    if requested:
        raise ValueError(
            f"Model '{requested}' is not a free model. "
            f"Available free models: {', '.join(_free_model_ids[:10])}..."
        )
    return DEFAULT_FREE_MODEL


# ---------------------------------------------------------------------------
# Anthropic <-> OpenAI conversion
# ---------------------------------------------------------------------------

def anthropic_to_openai(body: dict) -> dict:
    """Convert an Anthropic /v1/messages request to OpenAI chat-completions."""
    messages = []

    # system prompt
    system = body.get("system")
    if system:
        if isinstance(system, str):
            messages.append({"role": "system", "content": system})
        elif isinstance(system, list):
            # Anthropic system as content blocks — take text only
            parts = []
            for block in system:
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
            if parts:
                messages.append({"role": "system", "content": "\n".join(parts)})

    # conversation messages
    for m in body.get("messages", []):
        role = m.get("role", "user")
        content = m.get("content", "")
        # Anthropic content can be a string or array of blocks
        if isinstance(content, list):
            parts = []
            for block in content:
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "image":
                    # pass through image blocks; OpenRouter handles multimodal
                    parts.append(block)
            content = parts if len(parts) == 1 and isinstance(parts[0], str) else (parts[0] if len(parts) == 1 else parts)
            if not isinstance(content, str) and len(parts) > 1:
                # keep as array for multimodal
                content = [p if isinstance(p, dict) else {"type": "text", "text": p} for p in parts]
        messages.append({"role": role, "content": content})

    openai_body = {"messages": messages}

    # copy common params
    for src, dst in [
        ("max_tokens", "max_tokens"),
        ("temperature", "temperature"),
        ("top_p", "top_p"),
    ]:
        if src in body:
            openai_body[dst] = body[src]

    # stop_sequences -> stop
    if "stop_sequences" in body:
        openai_body["stop"] = body["stop_sequences"]

    return openai_body


def openai_to_anthropic(openai_body: dict, model: str, request_id: str) -> dict:
    """Convert an OpenAI chat-completion response to Anthropic /v1/messages."""
    choice = openai_body.get("choices", [{}])[0]
    msg = choice.get("message", {})
    content_text = msg.get("content", "")

    # Build Anthropic content blocks
    if isinstance(content_text, str):
        content = [{"type": "text", "text": content_text}]
    elif isinstance(content_text, list):
        content = content_text  # already blocks
    else:
        content = [{"type": "text", "text": str(content_text)}]

    # Map finish reason
    finish = choice.get("finish_reason", "stop")
    stop_reason = "end_turn"
    if finish == "length":
        stop_reason = "max_tokens"
    elif finish == "tool_calls":
        stop_reason = "tool_use"
    elif finish == "stop":
        stop_reason = "end_turn"

    usage = openai_body.get("usage", {})
    return {
        "id": f"msg_{openai_body.get('id', request_id)}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


# ---------------------------------------------------------------------------
# HTTP request handler
# ---------------------------------------------------------------------------

class ProxyHandler(http.server.BaseHTTPRequestHandler):
    """Single handler for OpenAI and Anthropic endpoints."""

    # Set by the server after init
    api_key: str = ""

    def log_message(self, format, *args):
        """Log to stderr."""
        print(f"[{self.log_date_time_string()}] {args[0]}", file=sys.stderr)

    def _send_json(self, status: int, data: dict | list):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, message: str, error_type: str = "invalid_request_error"):
        self._send_json(status, {
            "error": {
                "message": message,
                "type": error_type,
            }
        })

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw)

    def _proxy_to_openrouter(self, openai_body: dict, model: str) -> dict:
        """Send an OpenAI-formatted request to OpenRouter and return the response."""
        url = f"{OPENROUTER_BASE}/chat/completions"
        openai_body["model"] = model
        payload = json.dumps(openai_body).encode()

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/hafizanpurnama-dev/makefun-free-model-aggregator-02",
                "X-Title": "Free Model Aggregator",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())

    # ---- OpenAI endpoint ----

    def _handle_openai(self):
        try:
            body = self._read_body()
        except json.JSONDecodeError:
            return self._send_error(400, "Invalid JSON body")

        try:
            model = pick_model(body.get("model"))
        except ValueError as e:
            return self._send_error(400, str(e))

        try:
            response = self._proxy_to_openrouter(body, model)
        except urllib.error.HTTPError as e:
            error_body = e.read().decode(errors="replace")
            return self._send_error(e.code, f"OpenRouter error: {error_body[:500]}")
        except Exception as e:
            return self._send_error(502, f"Upstream error: {e}")

        self._send_json(200, response)

    # ---- Anthropic endpoint ----

    def _handle_anthropic(self):
        try:
            body = self._read_body()
        except json.JSONDecodeError:
            return self._send_error(400, "Invalid JSON body")

        try:
            model = pick_model(body.get("model"))
        except ValueError as e:
            return self._send_error(400, str(e))

        request_id = self.headers.get("X-Request-Id", "")

        try:
            openai_body = anthropic_to_openai(body)
            openai_response = self._proxy_to_openrouter(openai_body, model)
            anthropic_response = openai_to_anthropic(openai_response, model, request_id)
        except urllib.error.HTTPError as e:
            error_body = e.read().decode(errors="replace")
            return self._send_error(e.code, f"OpenRouter error: {error_body[:500]}")
        except Exception as e:
            return self._send_error(502, f"Upstream error: {e}")

        self._send_json(200, anthropic_response)

    # ---- Routing ----

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json(200, {
                "status": "ok",
                "free_model_count": len(_free_models),
                "default_model": DEFAULT_FREE_MODEL,
            })
        elif path == "/v1/models":
            self._send_json(200, {
                "object": "list",
                "data": [
                    {"id": mid, "object": "model", "owned_by": "openrouter"}
                    for mid in _free_model_ids
                ],
            })
        else:
            self._send_error(404, f"Not found: {path}")

    def do_POST(self):
        path = urlparse(self.path).path

        if self.api_key == "MISSING":
            return self._send_error(500, "OPENROUTER_API_KEY not set on server")

        # Lazy-load models on first POST
        try:
            ensure_models_loaded(self.api_key)
        except Exception as e:
            return self._send_error(500, f"Failed to load free models: {e}")

        if path == "/v1/chat/completions":
            self._handle_openai()
        elif path == "/v1/messages":
            self._handle_anthropic()
        else:
            self._send_error(404, f"Not found: {path}")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Request-Id, anthropic-version")
        self.end_headers()


class ThreadedHTTPServer(http.server.ThreadingHTTPServer):
    """HTTP server with threading support."""
    allow_reuse_address = True
    daemon_threads = True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("⚠  OPENROUTER_API_KEY not set — server will fail on POST requests.")
        print("   Set it: export OPENROUTER_API_KEY=sk-or-...")
        api_key = "MISSING"

    port = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[1] == "--port" else int(os.environ.get("PORT", 8080))
    host = os.environ.get("HOST", "0.0.0.0")

    # Pre-fetch models at startup if key is available
    if api_key != "MISSING":
        try:
            ensure_models_loaded(api_key)
            print(f"✓ Loaded {len(_free_models)} free models from OpenRouter")
        except Exception as e:
            print(f"⚠  Could not pre-load models: {e}")

    ProxyHandler.api_key = api_key

    server = ThreadedHTTPServer((host, port), ProxyHandler)
    print(f"✓ Free Model Aggregator listening on {host}:{port}")
    print(f"  OpenAI endpoint:   POST /v1/chat/completions")
    print(f"  Anthropic endpoint: POST /v1/messages")
    print(f"  Default model:      {DEFAULT_FREE_MODEL}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()