#!/usr/bin/env python3
"""new-api 渠道接入验证（stdlib only，零外部请求）。

模拟 new-api 添加「自定义渠道」时的真实调用序列，逐项断言网关契约：

    python tools/verify_newapi.py                                   # mock 网关 8946
    python tools/verify_newapi.py --base http://127.0.0.1:8947       # real 模式（BYOK）
    python tools/verify_newapi.py --token sk-amor-local              # 网关启用 --auth-token

它检查的正是 new-api 侧会踩到的点：
  · /v1/models 能否列出模型（渠道「获取模型列表」按钮）
  · 非流式 / 流式 chat 是否 OpenAI 形状（渠道测试 + 实际对话）
  · 是否返回 usage（new-api 按 usage 计费，缺 usage 会被记 0 或报错）
  · stream_options.include_usage 是否被容忍（新版 openai SDK 默认会带）
  · 裸模型名 / 未知模型的行为（渠道里手填模型名时最常见）
  · 错误信封是否 {"error":{message,type,code}}（new-api 靠它判定渠道故障）

退出码 0 = 可接。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

RESULTS: list[tuple[str, bool, str]] = []
TOKEN = ""


def _utf8() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            if (s.encoding or "").lower().replace("-", "") != "utf8":
                s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def call(path: str, body: dict | None = None, stream: bool = False,
         base: str = "") -> tuple[int, str]:
    url = base + path
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="GET" if body is None else "POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "text/event-stream" if stream else "application/json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def record(name: str, ok: bool, detail: str) -> None:
    RESULTS.append((name, ok, detail))


def error_envelope(raw: str) -> tuple[bool, str]:
    """new-api 只认 {"error":{message,type,code}} 形状。"""
    try:
        err = json.loads(raw).get("error")
    except (json.JSONDecodeError, AttributeError):
        return False, "not a JSON error body"
    if not isinstance(err, dict):
        return False, "missing error object"
    ok = all(k in err for k in ("message", "type", "code"))
    return ok, json.dumps(err, ensure_ascii=False)[:100]


def main() -> int:
    _utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8946")
    ap.add_argument("--model", default="amor-1", help="渠道里要填的模型名")
    ap.add_argument("--token", default="")
    ap.add_argument("--channel-model", default="gpt-4o-mini",
                    help="模拟 new-api 把渠道名/上游名透传过来的情况")
    args = ap.parse_args()

    global TOKEN
    TOKEN = args.token
    base = args.base.rstrip("/")

    # 1) 渠道「获取模型列表」
    st, raw = call("/v1/models", base=base)
    ids: list[str] = []
    if st == 200:
        try:
            ids = [m["id"] for m in json.loads(raw).get("data", [])]
        except (json.JSONDecodeError, KeyError, TypeError):
            ids = []
    record("GET /v1/models", st == 200 and bool(ids), f"HTTP {st} · {len(ids)} models")

    # 2) 非流式（new-api 渠道测试的典型 payload）
    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": "hello, who are you?"}],
        "temperature": 0.8,
        "max_tokens": 80,
        "stream": False,
    }
    st, raw = call("/v1/chat/completions", payload, base=base)
    ok, detail = False, f"HTTP {st} · {raw[:100]}"
    if st == 200:
        try:
            data = json.loads(raw)
            usage = data.get("usage") or {}
            ok = (data.get("object") == "chat.completion"
                  and isinstance(data.get("choices"), list) and data["choices"]
                  and data["choices"][0]["message"]["content"]
                  and data["choices"][0].get("finish_reason") == "stop"
                  and usage.get("total_tokens", 0) > 0)
            detail = (f"usage={usage.get('total_tokens')} "
                      f"finish={data['choices'][0].get('finish_reason')}")
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            ok = False
    record("非流式 chat + usage 计费锚点", ok, detail)

    # 3) 流式 + stream_options.include_usage（新版 openai SDK 默认行为）
    stream_payload = dict(payload, stream=True, stream_options={"include_usage": True})
    st, raw = call("/v1/chat/completions", stream_payload, stream=True, base=base)
    chunks, done, saw_content, saw_usage = 0, False, False, False
    for line in raw.splitlines():
        if not line.startswith("data: "):
            continue
        body = line[6:].strip()
        if body == "[DONE]":
            done = True
            continue
        try:
            obj = json.loads(body)
        except json.JSONDecodeError:
            continue
        chunks += 1
        if obj.get("object") == "chat.completion.chunk":
            delta = (obj.get("choices") or [{}])[0].get("delta") or {}
            if delta.get("content"):
                saw_content = True
            if obj.get("usage"):
                saw_usage = True
    record("流式 SSE 结构（chunk/[DONE]/content）",
           st == 200 and chunks > 0 and done and saw_content,
           f"HTTP {st} · {chunks} chunks · done={done}")
    record("流式末帧带 usage（含 include_usage 请求）",
           saw_usage, f"usage frame={saw_usage}")

    # 4) 裸模型名透传（渠道里手填 gpt-4o-mini 之类）
    st, raw = call("/v1/chat/completions", dict(payload, model=args.channel_model), base=base)
    ok = st == 200
    if ok:
        try:
            ok = bool(json.loads(raw)["choices"][0]["message"]["content"])
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            ok = False
    record(f"裸模型名 {args.channel_model} 可用", ok, f"HTTP {st}")

    # 5) 未知模型 → 404 model_not_found（渠道配错模型名时的信号）
    st, raw = call("/v1/chat/completions", dict(payload, model="no-such-model-xyz"), base=base)
    env_ok, env_detail = error_envelope(raw)
    record("未知模型 → 404 model_not_found", st == 404 and env_ok,
           f"HTTP {st} · {env_detail}")

    # 6) 空 messages（new-api 探活偶尔发空体）
    st, raw = call("/v1/chat/completions", {"model": args.model, "messages": []}, base=base)
    record("空 messages 不崩", st in (200, 400), f"HTTP {st}")

    # 7) moderation 端点（若渠道配置了审核）
    st, raw = call("/v1/moderations", {"input": "hello"}, base=base)
    record("POST /v1/moderations", st == 200, f"HTTP {st}")

    # 8) 鉴权（启用 token 时）
    if TOKEN:
        saved = TOKEN
        TOKEN = ""
        st, raw = call("/v1/chat/completions", payload, base=base)
        env_ok, env_detail = error_envelope(raw)
        record("缺 Bearer → 401 信封", st == 401 and env_ok, f"HTTP {st} · {env_detail}")
        TOKEN = saved

    print()
    failed = 0
    for name, ok, detail in RESULTS:
        print(f"[{'PASS' if ok else 'FAIL'}] {name:38s} ({detail})")
        if not ok:
            failed += 1
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} passed "
          f"· 参数：base={base} model={args.model} token={'yes' if TOKEN else 'no'}")
    if failed:
        print("\n有 FAIL：先按 docs/HANDOFF-TO-NEWAPI.md 第 6 节核对错误映射，再决定是否上线该渠道。")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
