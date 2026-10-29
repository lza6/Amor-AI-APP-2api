#!/usr/bin/env python3
"""Amor AI 1.8.8 (com.amorai.chat) → OpenAI 兼容 2API 网关。

零第三方依赖（stdlib only），支持三种跑法：

  mock（默认）  —— 全本地生成，零外部请求。用于协议验证、demo、压测。
  real          —— 转发到用户自己的 OpenAI 兼容端点（BYOK）。合规可生产。

启动：
    python server.py                     # mock，127.0.0.1:8946
    python server.py --mode real --api-key sk-xxx

接口：
    GET  /healthz
    GET  /v1/models
    POST /v1/chat/completions            # 支持 stream / non-stream
    POST /v1/moderations
    GET  /stats
    GET  /                              # demo 页
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import amor_client as ac

BASE_DIR = Path(__file__).resolve().parent
START_TS = time.time()


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "char"


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.total = 0
        self.errors = 0
        self.latencies: list[float] = []
        self.by_model: dict[str, int] = {}

    def record(self, ok: bool, latency: float, model: str) -> None:
        with self._lock:
            self.total += 1
            if not ok:
                self.errors += 1
            self.latencies.append(latency)
            if len(self.latencies) > 5000:
                del self.latencies[:2000]
            self.by_model[model] = self.by_model.get(model, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            lat = sorted(self.latencies)
            n = len(lat)

            def pct(p: float) -> float:
                if not n:
                    return 0.0
                idx = min(n - 1, int(round(p * (n - 1))))
                return round(lat[idx] * 1000, 1)

            return {
                "uptime_s": round(time.time() - START_TS, 1),
                "total": self.total,
                "errors": self.errors,
                "error_rate": round(self.errors / self.total, 4) if self.total else 0.0,
                "p50_ms": pct(0.50),
                "p95_ms": pct(0.95),
                "p99_ms": pct(0.99),
                "by_model": dict(self.by_model),
            }


class Gateway:
    """模型名 → 角色的解析 + 上游调用统一入口。"""

    def __init__(self, characters: list[dict[str, Any]], upstream, mode: str,
                 auth_token: str | None = None):
        self.characters = characters
        self.upstream = upstream
        self.mode = mode
        # 可选的本地鉴权：设置后 /v1/* 需要 Authorization: Bearer <token>
        self.auth_token = auth_token or None
        self.metrics = Metrics()
        self._index: dict[str, dict[str, Any]] = {}
        for ch in characters:
            cid = ch["id"]
            self._index[f"amor-{cid}"] = ch
            self._index[f"amor/{cid}"] = ch
            self._index[slugify(ch.get("name", ""))] = ch
            self._index[f"amor-{slugify(ch.get('name', ''))}"] = ch

    def resolve(self, model: str) -> dict[str, Any] | None:
        key = (model or "").strip().lower()
        if key in self._index:
            return self._index[key]
        # 允许 "amor-7-nicole" 之类的带名后缀
        m = re.match(r"^amor[-/](\d+)", key)
        if m:
            return self._index.get(f"amor-{m.group(1)}")
        # 裸模型名（gpt-4o-mini 等）→ 默认第一个角色，保证渠道可用
        if key.startswith("gpt-") or key in {"default", "amor"}:
            return self.characters[0] if self.characters else None
        return None

    def models_payload(self) -> dict[str, Any]:
        data = []
        for ch in self.characters:
            data.append({
                "id": f"amor-{ch['id']}",
                "object": "model",
                "created": int(START_TS),
                "owned_by": "amor-ai",
                "meta": {
                    "name": ch.get("name"),
                    "gender": ch.get("gender"),
                    "age": ch.get("age"),
                    "role": ch.get("role"),
                    "is_premium": ch.get("is_premium", False),
                    "alias": slugify(ch.get("name", "")),
                },
            })
        return {"object": "list", "data": data}

    def chat(self, body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """返回 (model_id, openai_style_completion)。"""
        model = body.get("model") or ac.DEFAULT_MODEL
        # 允许 "amor-7@<upstream-model>" 指定透传给上游的模型名（默认 gpt-4o-mini）
        upstream_model = None
        if "@" in str(model):
            model, upstream_model = str(model).split("@", 1)
        character = self.resolve(model)
        if character is None:
            raise KeyError(f"unknown model: {model}")

        incoming = body.get("messages") or []
        system_override = next(
            (m["content"] for m in incoming if m.get("role") == "system" and m.get("content")),
            None,
        )
        convo = [m for m in incoming if m.get("role") in ("user", "assistant")]
        if not convo and not system_override:
            convo = [{"role": "user", "content": "Hello!"}]

        messages = ac.build_messages(character, convo)
        if system_override:
            messages[0] = {"role": "system", "content": system_override}

        overrides = {
            k: body[k] for k in
            ("temperature", "max_tokens", "frequency_penalty", "presence_penalty")
            if body.get(k) is not None
        }
        if body.get("max_tokens") is None and body.get("max_completion_tokens"):
            overrides["max_tokens"] = body["max_completion_tokens"]
        if upstream_model:
            overrides["model"] = upstream_model

        content = self.upstream.chat(messages, **overrides)
        prompt_tokens = sum(len(m["content"]) for m in messages) // 4
        completion_tokens = len(content) // 4
        model_id = f"amor-{character['id']}"
        payload = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_id,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
        return model_id, payload


class Handler(BaseHTTPRequestHandler):
    server_version = "amor-2api/1.0"
    gateway: Gateway = None  # type: ignore[assignment]

    def log_message(self, fmt: str, *args) -> None:  # 静音，压测时别刷屏
        if os.environ.get("AMOR_VERBOSE"):
            super().log_message(fmt, *args)

    # ---------------- 基础响应工具 ----------------
    def _send_json(self, code: int, obj: dict[str, Any]) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def _error(self, code: int, message: str, etype: str = "invalid_request_error") -> None:
        self._send_json(code, {"error": {"message": message, "type": etype, "code": code}})

    def _read_json(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    # ---------------- 路由 ----------------
    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/v1/") and not self._authorized():
            self._error(401, "invalid api key", "authentication_error")
            return
        if path in ("/healthz", "/health"):
            self._send_json(200, {"status": "ok", "mode": self.gateway.mode,
                                  "characters": len(self.gateway.characters),
                                  "uptime_s": round(time.time() - START_TS, 1)})
        elif path == "/v1/models":
            self._send_json(200, self.gateway.models_payload())
        elif path == "/stats":
            self._send_json(200, self.gateway.metrics.snapshot())
        elif path in ("/", "/index.html", "/demo", "/demo.html"):
            self._serve_demo()
        else:
            self._error(404, f"no route: {path}", "not_found")

    def _serve_demo(self) -> None:
        demo = BASE_DIR / "demo.html"
        if not demo.exists():
            self._error(404, "demo.html missing", "not_found")
            return
        raw = demo.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _authorized(self) -> bool:
        token = getattr(self.gateway, "auth_token", None)
        if not token:
            return True
        header = self.headers.get("Authorization") or ""
        return header.strip() == f"Bearer {token}"

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/v1/") and not self._authorized():
            self._error(401, "invalid api key", "authentication_error")
            return
        body = self._read_json()
        if body is None:
            self._error(400, "invalid JSON body")
            return
        if path == "/v1/chat/completions":
            self._handle_chat(body)
        elif path == "/v1/moderations":
            self._handle_moderation(body)
        elif path == "/stats/reset":
            self.gateway.metrics.reset()
            self._send_json(200, {"ok": True})
        else:
            self._error(404, f"no route: {path}", "not_found")

    # ---------------- 业务处理 ----------------
    def _handle_chat(self, body: dict[str, Any]) -> None:
        t0 = time.time()
        stream = bool(body.get("stream"))
        model_req = body.get("model") or ac.DEFAULT_MODEL
        try:
            model_id, payload = self.gateway.chat(body)
        except KeyError as exc:
            self.gateway.metrics.record(False, time.time() - t0, str(model_req))
            self._error(404, str(exc).strip("'\""), "model_not_found")
            return
        except ac.UpstreamError as exc:
            self.gateway.metrics.record(False, time.time() - t0, str(model_req))
            self._error(exc.status if 400 <= exc.status < 600 else 502,
                        exc.message, "upstream_error")
            return
        except Exception as exc:  # 兜底，绝不静默
            self.gateway.metrics.record(False, time.time() - t0, str(model_req))
            self._error(500, f"{type(exc).__name__}: {exc}", "server_error")
            return

        if not stream:
            self.gateway.metrics.record(True, time.time() - t0, model_id)
            self._send_json(200, payload)
            return

        self._stream_out(payload)
        self.gateway.metrics.record(True, time.time() - t0, model_id)

    def _stream_out(self, payload: dict[str, Any]) -> None:
        """把非流式结果包装成 SSE（上游本身非流式；此为兼容层）。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        # 流式响应无 Content-Length，必须显式关闭，否则客户端会挂到超时
        self.close_connection = True

        cid = payload["id"]
        model = payload["model"]
        created = payload["created"]
        content = payload["choices"][0]["message"]["content"]
        prefix = {
            "id": cid, "object": "chat.completion.chunk", "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }
        try:
            self.wfile.write(b"data: " + json.dumps(prefix, ensure_ascii=False).encode() + b"\n\n")
            self.wfile.flush()
            for chunk in _chunk_text(content):
                piece = {
                    "id": cid, "object": "chat.completion.chunk", "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
                }
                self.wfile.write(b"data: " + json.dumps(piece, ensure_ascii=False).encode() + b"\n\n")
                self.wfile.flush()
            done = {
                "id": cid, "object": "chat.completion.chunk", "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": payload.get("usage"),
            }
            self.wfile.write(b"data: " + json.dumps(done, ensure_ascii=False).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _handle_moderation(self, body: dict[str, Any]) -> None:
        t0 = time.time()
        text = body.get("input")
        if isinstance(text, list):
            text = " ".join(str(x) for x in text)
        try:
            result = self.gateway.upstream.moderate(text or "")
        except ac.UpstreamError as exc:
            self.gateway.metrics.record(False, time.time() - t0, "moderation")
            self._error(502, exc.message, "upstream_error")
            return
        except Exception as exc:
            self.gateway.metrics.record(False, time.time() - t0, "moderation")
            self._error(500, f"{type(exc).__name__}: {exc}", "server_error")
            return
        self.gateway.metrics.record(True, time.time() - t0, "moderation")
        self._send_json(200, result)


def _chunk_text(text: str, size: int = 6) -> list[str]:
    """按词/小块切分，模拟打字机流式。"""
    words = re.findall(r"\S+\s*", text) or [text]
    out: list[str] = []
    buf = ""
    for w in words:
        buf += w
        if len(buf) >= size:
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out


class QuietServer(ThreadingHTTPServer):
    """压测时客户端断连（WinError 10054）不应刷栈。"""
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address) -> None:
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


def build_gateway(args) -> Gateway:
    characters = ac.load_characters(Path(args.characters) if args.characters else None)
    if args.mode == "real":
        upstream = ac.Upstream(
            api_key=args.api_key or os.environ.get("OPENAI_API_KEY", ""),
            base_url=args.base_url,
            timeout=args.timeout,
            proxy=args.proxy,
        )
    else:
        upstream = ac.MockUpstream(latency_ms=args.mock_latency, jitter_ms=args.mock_jitter)
    return Gateway(characters, upstream, args.mode, args.auth_token)


def main() -> None:
    ap = argparse.ArgumentParser(description="Amor AI 1.8.8 → OpenAI 兼容 2API")
    ap.add_argument("--mode", choices=("mock", "real"), default=os.environ.get("AMOR_MODE", "mock"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8946)
    ap.add_argument("--api-key", default=None, help="real 模式的上游 key（BYOK）")
    ap.add_argument("--base-url", default=None, help="上游 base url，默认 https://api.openai.com/v1")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--proxy", default=None,
                    help="显式上游代理，如 http://127.0.0.1:10808；默认忽略环境代理直连")
    ap.add_argument("--mock-latency", type=int, default=120)
    ap.add_argument("--mock-jitter", type=int, default=80)
    ap.add_argument("--characters", default=None)
    ap.add_argument("--auth-token", default=os.environ.get("AMOR_TOKEN"),
                    help="可选的本地鉴权 token；设置后 /v1/* 需 Authorization: Bearer <token>")
    args = ap.parse_args()

    gateway = build_gateway(args)
    Handler.gateway = gateway
    srv = QuietServer((args.host, args.port), Handler)
    print(f"[amor-2api] mode={args.mode} listen=http://{args.host}:{args.port} "
          f"characters={len(gateway.characters)}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[amor-2api] bye", flush=True)
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
