#!/usr/bin/env python3
"""Amor 2API 压测脚本（stdlib only）。

用法：
    python bench.py --base http://127.0.0.1:8946 --concurrency 8 --requests 80
    python bench.py --mode stream --concurrency 4 --requests 40
    python bench.py --model amor-7 --json out.json

跑的是本网关（mock 或 real）。mock 模式下的数字只反映网关自身开销，
不能当作上游 AI 延迟——报告里必须区分。
"""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

PROMPTS = [
    "hi, how was your day?",
    "I missed you today",
    "tell me something sweet",
    "what are you doing right now?",
    "I had a rough day",
    "you are so cute",
    "do you like me?",
    "good night baby",
]


def one_request(base: str, model: str, stream: bool, timeout: float) -> dict[str, Any]:
    body = {
        "model": model,
        "stream": stream,
        "messages": [{"role": "user", "content": PROMPTS[uuid.uuid4().int % len(PROMPTS)]}],
    }
    req = urllib.request.Request(
        f"{base}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode("utf-8", "ignore")
        latency = time.time() - t0
        if stream:
            ok = "[DONE]" in payload and "chat.completion.chunk" in payload
        else:
            try:
                data = json.loads(payload)
                ok = bool(data["choices"][0]["message"]["content"])
            except Exception:
                ok = False
        return {"ok": ok, "latency": latency, "err": "" if ok else "malformed_response"}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "latency": time.time() - t0,
                "err": f"http_{exc.code}:{exc.read().decode('utf-8', 'ignore')[:120]}"}
    except Exception as exc:
        return {"ok": False, "latency": time.time() - t0, "err": f"{type(exc).__name__}:{exc}"}


def run(base: str, model: str, stream: bool, concurrency: int, total: int,
        timeout: float) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    lock = threading.Lock()
    counter = {"n": 0}

    def worker() -> None:
        while True:
            with lock:
                if counter["n"] >= total:
                    return
                counter["n"] += 1
            r = one_request(base, model, stream, timeout)
            with lock:
                results.append(r)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(concurrency)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - t0

    oks = [r for r in results if r["ok"]]
    lat = sorted(r["latency"] for r in oks)
    errs: dict[str, int] = {}
    for r in results:
        if not r["ok"]:
            key = (r["err"] or "unknown").split(":")[0]
            errs[key] = errs.get(key, 0) + 1

    def pct(p: float) -> float:
        if not lat:
            return 0.0
        return round(lat[min(len(lat) - 1, int(round(p * (len(lat) - 1))))] * 1000, 1)

    return {
        "mode": "stream" if stream else "sync",
        "model": model,
        "concurrency": concurrency,
        "requests": total,
        "success": len(oks),
        "failed": len(results) - len(oks),
        "error_rate": round((len(results) - len(oks)) / max(1, len(results)), 4),
        "errors": errs,
        "elapsed_s": round(elapsed, 2),
        "rps": round(len(results) / elapsed, 1) if elapsed else 0.0,
        "p50_ms": pct(0.50),
        "p95_ms": pct(0.95),
        "p99_ms": pct(0.99),
        "mean_ms": round(statistics.fmean(lat) * 1000, 1) if lat else 0.0,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Amor 2API 压测")
    ap.add_argument("--base", default="http://127.0.0.1:8946")
    ap.add_argument("--model", default="amor-1")
    ap.add_argument("--mode", choices=("sync", "stream"), default="sync")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--requests", type=int, default=64)
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--rounds", type=int, default=1, help="重复轮数（每轮重置统计看稳定性）")
    ap.add_argument("--json", default=None, help="把结果写入文件")
    args = ap.parse_args()

    all_rounds = []
    for i in range(args.rounds):
        print(f"--- round {i + 1}/{args.rounds} ({args.mode}, c={args.concurrency}, "
              f"n={args.requests}) ---", flush=True)
        r = run(args.base, args.model, args.mode == "stream", args.concurrency,
                args.requests, args.timeout)
        all_rounds.append(r)
        print(json.dumps(r, ensure_ascii=False, indent=2), flush=True)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(all_rounds, fh, ensure_ascii=False, indent=2)
        print(f"saved -> {args.json}")


if __name__ == "__main__":
    main()
