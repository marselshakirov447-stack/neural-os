#!/usr/bin/env python3
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "dashboard" / "index.html"
MAX_BODY_SIZE = 64 * 1024
MAX_MESSAGES = 20
MAX_MESSAGE_LENGTH = 8000
PROVIDERS = {
    "openai": {
        "key": "OPENAI_API_KEY",
        "model": "gpt-4.1-mini",
        "url": "https://api.openai.com/v1/chat/completions",
    },
    "claude": {
        "key": "ANTHROPIC_API_KEY",
        "model": "claude-sonnet-4-20250514",
        "url": "https://api.anthropic.com/v1/messages",
    },
}


def load_dotenv():
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            value = value.strip().strip("\"'")
            if name and name.replace("_", "").isalnum():
                os.environ.setdefault(name, value)
    PROVIDERS["openai"]["model"] = os.environ.get(
        "OPENAI_MODEL", PROVIDERS["openai"]["model"]
    )
    PROVIDERS["claude"]["model"] = os.environ.get(
        "ANTHROPIC_MODEL", PROVIDERS["claude"]["model"]
    )


def request_completion(provider_name, messages):
    provider = PROVIDERS[provider_name]
    api_key = os.environ.get(provider["key"])
    if not api_key:
        raise ValueError(
            f"{provider_name} is not configured. Add {provider['key']} to .env."
        )

    if provider_name == "openai":
        payload = {"model": provider["model"], "messages": messages}
        headers = {"Authorization": f"Bearer {api_key}"}
    else:
        payload = {
            "model": provider["model"],
            "max_tokens": 2048,
            "messages": messages,
        }
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }

    request = Request(
        provider["url"],
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        try:
            details = json.loads(error.read().decode("utf-8"))
            message = details.get("error", {}).get("message")
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            message = None
        raise RuntimeError(message or f"Provider returned HTTP {error.code}.") from None
    except URLError as error:
        raise RuntimeError(f"Could not reach the provider: {error.reason}.") from None
    except TimeoutError:
        raise RuntimeError("The provider request timed out.") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("The provider returned an invalid response.") from None

    if provider_name == "openai":
        choices = result.get("choices", [])
        reply = choices[0].get("message", {}).get("content") if choices else None
    else:
        content = result.get("content", [])
        reply = "\n".join(
            block["text"]
            for block in content
            if block.get("type") == "text" and isinstance(block.get("text"), str)
        )
    if not isinstance(reply, str) or not reply:
        raise RuntimeError("The provider returned no text response.")
    return reply


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/status":
            self.send_json(
                200,
                {
                    name: bool(os.environ.get(config["key"]))
                    for name, config in PROVIDERS.items()
                },
            )
            return
        if self.path in ("/", "/dashboard/index.html"):
            body = INDEX.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_json(404, {"error": "Not found."})

    def do_POST(self):
        if self.path != "/api/chat":
            self.send_json(404, {"error": "Not found."})
            return
        origin = self.headers.get("Origin")
        allowed_origins = {
            f"http://127.0.0.1:{self.server.server_port}",
            f"http://localhost:{self.server.server_port}",
        }
        if origin and origin not in allowed_origins:
            self.send_json(403, {"error": "Cross-origin requests are not allowed."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(400, {"error": "Invalid Content-Length."})
            return
        if length <= 0 or length > MAX_BODY_SIZE:
            self.send_json(413, {"error": "Request body must be between 1 byte and 64 KB."})
            return
        if self.headers.get_content_type() != "application/json":
            self.send_json(415, {"error": "Content-Type must be application/json."})
            return
        try:
            data = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"error": "Request body must be valid JSON."})
            return
        provider = data.get("provider") if isinstance(data, dict) else None
        messages = data.get("messages") if isinstance(data, dict) else None
        if not isinstance(provider, str) or provider not in PROVIDERS:
            self.send_json(400, {"error": "Choose either openai or claude."})
            return
        if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES:
            self.send_json(
                400,
                {"error": f"Send between 1 and {MAX_MESSAGES} messages."},
            )
            return
        normalized = []
        for message in messages:
            if (
                not isinstance(message, dict)
                or message.get("role") not in ("user", "assistant")
                or not isinstance(message.get("content"), str)
                or not message["content"].strip()
                or len(message["content"]) > MAX_MESSAGE_LENGTH
            ):
                self.send_json(
                    400,
                    {
                        "error": (
                            "Each message needs a valid role and text of at most "
                            "8000 characters."
                        )
                    },
                )
                return
            normalized.append(
                {"role": message["role"], "content": message["content"]}
            )
        try:
            reply = request_completion(provider, normalized)
        except ValueError as error:
            self.send_json(503, {"error": str(error)})
            return
        except RuntimeError as error:
            self.send_json(502, {"error": str(error)})
            return
        self.send_json(200, {"reply": reply})

    def log_message(self, format, *args):
        pass


def main():
    load_dotenv()
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"NEURAL OS is available at http://127.0.0.1:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping NEURAL OS.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
