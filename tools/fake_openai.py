#!/usr/bin/env python3
"""本地假 OpenAI 上游（测试夹具，零外部请求）。

用于验证 server.py 的 real 模式链路：请求头、请求体、响应解析、错误映射。
绝不代表任何真实上游行为。

    python tools/fake_openai.py --port 8950
    # 行为开关（通过 model 名触发）
    #   fail-500   -> 上游 500 + JSON error
    #   malformed  -> 200 但响应体不是合法 OpenAI JSON
    #   slow       -> 睡 3 秒后正常返回
"""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SEEN: list[dict] = []


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a) -> None:
        pass

    def _json(self, code: int, obj: dict) -> None:
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n).decode() or "{}")
        auth = self.headers.get("Authorization", "")
        SEEN.append({"path": self.path, "auth": auth, "body": body})
        if self.path.endswith("/chat/completions"):
            model = body.get("model", "")
            if model == "fail-500":
                self._json(500, {"error": {"message": "internal boom"}})
                return
            if model == "slow":
                time.sleep(3)
            if model == "malformed":
                raw = b"not-json-at-all"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            msgs = body.get("messages") or []
            last = msgs[-1]["content"] if msgs else ""
            self._json(200, {
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "model": model,
                "choices": [{"index": 0, "message": {
                    "role": "assistant",
                    "content": f"FAKE[{model}] echo: {last[:40]}",
                }, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            })
        elif self.path.endswith("/moderations"):
            text = body.get("input", "")
            self._json(200, {
                "id": "modr-fake", "model": "omni-moderation-latest",
                "results": [{"flagged": "nude" in str(text).lower(),
                             "categories": {"sexual": "nude" in str(text).lower()},
                             "category_scores": {"sexual": 0.9 if "nude" in str(text).lower() else 0.01}}],
            })
        else:
            self._json(404, {"error": {"message": "no route"}})

    def do_GET(self) -> None:
        if self.path == "/__seen":
            self._json(200, {"count": len(SEEN), "calls": SEEN[-5:]})
        else:
            self._json(404, {"error": {"message": "no route"}})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8950)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Fake)
    print(f"[fake-openai] http://127.0.0.1:{args.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
