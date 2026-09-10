from __future__ import annotations

import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
HTML_PATH = ROOT / "web_chat.html"


def load_env_file() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


load_env_file()
from mcp.bfe_mcp_proxy import call_gateway  # noqa: E402


class ChatHandler(BaseHTTPRequestHandler):
    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/":
            self.send_error(404)
            return
        body = HTML_PATH.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/api/ask":
            self.send_error(404)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size > 20_000:
                self.send_json(413, {"error": "Question is too long"})
                return
            request = json.loads(self.rfile.read(size))
            question = request.get("question", "").strip()
            if not question:
                self.send_json(400, {"error": "Please enter a question"})
                return
            gateway_result = call_gateway(question, threading.get_ident())
            response = gateway_result
            try:
                response = json.loads(gateway_result["content"][0]["text"])
            except (KeyError, IndexError, TypeError, json.JSONDecodeError):
                pass
            self.send_json(200, {"question": question, "result": response})
        except Exception as exc:
            self.send_json(502, {"error": str(exc)})

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[web] {format % args}")


def main() -> None:
    host = "127.0.0.1"
    port = 8765
    server = ThreadingHTTPServer((host, port), ChatHandler)
    url = f"http://{host}:{port}/"
    print(f"Energy Gateway chat: {url}")
    print("Press Ctrl+C to stop the server.")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping chat server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
