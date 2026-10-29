#!/usr/bin/env python3
"""边界/错误路径回归（stdlib only，零外部请求）。

用法：
    python tools/test_edges.py                       # 只打 mock 网关（默认 8946）
    python tools/test_edges.py --base http://127.0.0.1:8947 --upstream http://127.0.0.1:8950
                                                     # 额外验证 real 模式的上游错误映射
    python tools/test_edges.py --token secret        # 网关启用 --auth-token 时校验 401 分支

退出码 0 = 全通过。所有断言都基于真实 HTTP 响应，不做静态推断。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

RESULTS: list[tuple[str, bool, str]] = []
# 网关启用 --auth-token 时，常规用例也要带 token；显式传 token="" 表示"故意不带"
DEFAULT_TOKEN = ""


def _utf8() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            if (s.encoding or "").lower().replace("-", "") != "utf8":
                s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def call(method: str, url: str, body: object | bytes | None = None,
         token: str | None = None, timeout: float = 30.0) -> tuple[int, str]:
    """返回 (status, raw_text)。连接被断开时返回 (0, reason)。

    token=None 用全局 DEFAULT_TOKEN；token="" 表示故意不带凭证（验 401）。
    """
    if isinstance(body, bytes):
        data = body
    elif body is None:
        data = None
    else:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    token = DEFAULT_TOKEN if token is None else token
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # 断连/协议错误都必须显式失败，不许静默
        return 0, f"{type(exc).__name__}: {exc}"


def check(name: str, expect: int | tuple[int, ...], got: int, raw: str,
          needle: str = "", forbid_500: bool = True) -> None:
    wants = expect if isinstance(expect, tuple) else (expect,)
    ok = got in wants
    if ok and needle:
        ok = needle in raw
    if ok and forbid_500 and got == 500 and 500 not in wants:
        ok = False
    detail = f"HTTP {got}" + (f" · {raw[:90]}" if raw else "")
    RESULTS.append((name, ok, detail))


def chat_body(**kw):
    body = {"model": "amor-1", "messages": [{"role": "user", "content": "hi"}]}
    body.update(kw)
    return body


def run_gateway_edges(base: str, token: str) -> None:
    url = base + "/v1/chat/completions"

    # ---- 请求体结构 ----
    st, raw = call("POST", url, b"{not json")
    check("invalid JSON body -> 400", 400, st, raw)
    st, raw = call("POST", url, b"[1,2,3]")
    check("JSON array body -> 400 (not drop)", 400, st, raw)
    st, raw = call("POST", url, b'"hi"')
    check("JSON string body -> 400 (not drop)", 400, st, raw)
    st, raw = call("POST", url, chat_body(messages={"role": "user", "content": "x"}))
    check("messages object -> 400", 400, st, raw)
    st, raw = call("POST", url, chat_body(messages=["hi"]))
    check("messages [str] -> 400", 400, st, raw)
    st, raw = call("POST", url, chat_body(messages=[None]))
    check("messages [null] -> 400", 400, st, raw)
    st, raw = call("POST", url, chat_body(messages=[{"role": "user", "content": 123}]))
    check("content int -> 400", 400, st, raw)
    st, raw = call("POST", url, chat_body(model=123))
    check("model int -> 400", 400, st, raw)
    st, raw = call("POST", url, chat_body(model={"a": 1}))
    check("model object -> 400", 400, st, raw)

    # ---- 参数类型 ----
    st, raw = call("POST", url, chat_body(max_tokens="abc"))
    check("max_tokens str -> 400", 400, st, raw)
    st, raw = call("POST", url, chat_body(max_tokens=-5))
    check("max_tokens negative -> 400", 400, st, raw)
    st, raw = call("POST", url, chat_body(max_tokens=1.5))
    check("max_tokens fractional -> 400", 400, st, raw)
    st, raw = call("POST", url, chat_body(temperature="hot"))
    check("temperature str -> 400", 400, st, raw)

    # ---- 路由/模型 ----
    st, raw = call("POST", url, chat_body(model="nope-999"))
    check("unknown model -> 404", 404, st, raw, needle="model_not_found")
    st, raw = call("GET", base + "/v1/not-a-route")
    check("GET unknown route -> 404", 404, st, raw)
    st, raw = call("POST", base + "/v1/not-a-route", {})
    check("POST unknown route -> 404", 404, st, raw)

    # ---- 正常路径 ----
    st, raw = call("POST", url, chat_body())
    ok = st == 200
    payload = {}
    if ok:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            ok = False
    if ok:
        ok = (payload.get("object") == "chat.completion"
              and payload["choices"][0]["message"]["role"] == "assistant"
              and bool(payload["choices"][0]["message"]["content"])
              and payload.get("usage"))
    RESULTS.append(("sync chat -> 200 + OpenAI shape", ok, raw[:90]))

    st, raw = call("POST", url, chat_body(stream=True))
    ok = st == 200 and "data: " in raw and "[DONE]" in raw and "chat.completion.chunk" in raw
    RESULTS.append(("stream chat -> SSE + [DONE]", ok, raw[:60].replace("\n", "\\n")))

    st, raw = call("GET", base + "/v1/models")
    n = 0
    if st == 200:
        try:
            n = len(json.loads(raw).get("data", []))
        except json.JSONDecodeError:
            n = 0
    check("/v1/models lists characters", 200, st, raw, needle='"object": "list"')
    RESULTS.append(("models count > 0", n > 0, f"{n} models"))

    st, raw = call("GET", base + "/health")
    check("/health ok", 200, st, raw, needle='"status": "ok"')

    # ---- moderation ----
    murl = base + "/v1/moderations"
    st, raw = call("POST", murl, {"input": "hello there"})
    check("moderation normal -> 200", 200, st, raw, needle="results")
    st, raw = call("POST", murl, {"input": {"a": 1}})
    check("moderation input object -> 400", 400, st, raw)
    st, raw = call("POST", murl, {"input": ["ok", 5]})
    check("moderation input mixed array -> 400", 400, st, raw)

    # ---- 超大请求体 ----
    big = json.dumps(chat_body(messages=[{"role": "user", "content": "x" * (9 * 1024 * 1024)}])).encode()
    st, raw = call("POST", url, big)
    check("body > 8MB -> 413", 413, st, raw)

    # ---- 鉴权分支 ----
    if token:
        st, raw = call("POST", url, chat_body(), token="")
        check("no token -> 401", 401, st, raw, needle="authentication_error")
        st, raw = call("POST", url, chat_body(), token=token)
        check("valid token -> 200", 200, st, raw)
        st, raw = call("POST", url, chat_body(), token="wrong-token")
        check("wrong token -> 401", 401, st, raw)


def run_upstream_edges(base: str, upstream: str, token: str) -> None:
    url = base + "/v1/chat/completions"

    def seen() -> list[dict]:
        st, raw = call("GET", upstream + "/__seen")
        if st != 200:
            return []
        try:
            return json.loads(raw).get("calls", [])
        except json.JSONDecodeError:
            return []

    before = len(seen())

    st, raw = call("POST", url, chat_body(model="amor-1@gpt-4o-mini"))
    check("real mode passthrough -> 200", 200, st, raw, needle="FAKE[gpt-4o-mini]")

    calls = seen()
    if len(calls) > before:
        last = calls[-1]
        body = last.get("body", {})
        msgs = body.get("messages") or []
        auth_ok = last.get("auth", "").startswith("Bearer ")
        RESULTS.append(("upstream got Bearer auth", auth_ok, last.get("auth", "")[:24] + "..."))
        RESULTS.append(("app params replicated",
                        body.get("max_tokens") == 120
                        and abs(float(body.get("temperature", 0)) - 1.0) < 1e-9
                        and abs(float(body.get("presence_penalty", 0)) - 1.0) < 1e-9,
                        json.dumps({k: body.get(k) for k in
                                    ("model", "max_tokens", "temperature", "presence_penalty")})))
        RESULTS.append(("persona prompt is messages[0]",
                        bool(msgs) and msgs[0].get("role") in ("user", "system")
                        and "You are" in (msgs[0].get("content") or ""),
                        (msgs[0].get("content") or "")[:60] if msgs else "no messages"))
    else:
        RESULTS.append(("upstream call captured", False, "no call recorded by fake upstream"))

    # 历史裁剪：20 条历史 → 上游只应看到 1(persona) + 6
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(20)]
    st, raw = call("POST", url, chat_body(model="amor-1@gpt-4o-mini", messages=history))
    calls = seen()
    n = len(((calls[-1] or {}).get("body") or {}).get("messages") or []) if calls else 0
    RESULTS.append(("history trimmed to 6 turns", st == 200 and n == 7, f"upstream messages={n}"))

    # 上游 500 透传
    st, raw = call("POST", url, chat_body(model="amor-1@fail-500"))
    check("upstream 500 -> 500 (透传)", 500, st, raw, forbid_500=False)
    # 上游响应格式错
    st, raw = call("POST", url, chat_body(model="amor-1@malformed"))
    check("malformed upstream -> 502", 502, st, raw, needle="malformed upstream response")
    # 上游慢但未超时
    st, raw = call("POST", url, chat_body(model="amor-1@slow"), token=tag, timeout=30)
    check("slow upstream (3s) -> 200", 200, st, raw)


def main() -> int:
    _utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8946")
    ap.add_argument("--upstream", default=None,
                    help="假上游 base（tools/fake_openai.py）；给了才跑 real 模式错误映射")
    ap.add_argument("--token", default="")
    args = ap.parse_args()

    global DEFAULT_TOKEN
    DEFAULT_TOKEN = args.token
    run_gateway_edges(args.base, args.token)
    if args.upstream:
        run_upstream_edges(args.base, args.upstream, args.token)

    print()
    failed = 0
    for name, ok, detail in RESULTS:
        print(f"[{'PASS' if ok else 'FAIL'}] {name:34s} ({detail})")
        if not ok:
            failed += 1
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
