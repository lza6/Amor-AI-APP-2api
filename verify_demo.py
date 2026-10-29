#!/usr/bin/env python3
"""demo 页真实浏览器验证（Playwright）。

覆盖路径：打开页面 → 选角色 → 发消息（流式）→ 断言气泡 → 前端并发压测 → 断言统计。
用法：
    python verify_demo.py
    python verify_demo.py --url http://127.0.0.1:8946/ --out static/demo.png
退出码 0 = 全通过。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def _utf8_stdout() -> None:
    """Windows 控制台默认 GBK，角色名/emoji 会炸编码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if (stream.encoding or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main() -> int:
    _utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8946/")
    ap.add_argument("--out", default="static/demo.png")
    ap.add_argument("--timeout", type=int, default=30000)
    ap.add_argument("--token", default="", help="网关启用 --auth-token 时填入同一个 token")
    args = ap.parse_args()

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    checks: list[tuple[str, bool, str]] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 860})
        page.goto(args.url, wait_until="networkidle", timeout=args.timeout)
        if args.token:
            page.fill("#token", args.token)
            page.dispatch_event("#token", "change")
            page.wait_for_selector(".char", timeout=args.timeout)

        # 1) 顶部状态：mode 与角色数
        mode = page.inner_text("#modePill")
        chars = page.inner_text("#charPill")
        checks.append(("header mode pill", "mode:" in mode, mode))
        checks.append(("character count > 0", any(c.isdigit() for c in chars), chars))

        # 2) 角色列表渲染
        page.wait_for_selector(".char", timeout=args.timeout)
        count = page.eval_on_selector_all(".char", "els => els.length")
        checks.append(("charlist rendered", count > 10, f"{count} items"))

        # 3) 选角色 → 对话区出现开场白
        page.click(".char")
        page.wait_for_selector(".msg.assistant", timeout=args.timeout)
        opening = page.inner_text(".msg.assistant .bubble")
        checks.append(("opening message", "Hi, I'm" in opening, opening[:60]))

        # 4) 发消息（流式）→ 有 assistant 回复且非空
        page.fill("#input", "hey, I missed you today")
        page.click("#sendBtn")
        page.wait_for_function(
            "() => { const b = document.querySelectorAll('.msg.assistant .bubble');"
            " return b.length >= 2 && b[b.length-1].textContent.trim().length > 3; }",
            timeout=args.timeout,
        )
        reply = page.eval_on_selector_all(
            ".msg.assistant .bubble", "els => els[els.length-1].textContent")
        checks.append(("streamed reply non-empty", len(reply.strip()) > 3, reply[:70]))
        latency = page.inner_text("#latPill")
        checks.append(("latency pill updated", "ms" in latency, latency))

        # 5) 非流式路径
        page.uncheck("#streamChk")
        page.fill("#input", "tell me something sweet")
        page.click("#sendBtn")
        page.wait_for_function(
            "() => { const b = document.querySelectorAll('.msg.assistant .bubble');"
            " return b.length >= 3 && b[b.length-1].textContent.trim().length > 3; }",
            timeout=args.timeout,
        )
        reply2 = page.eval_on_selector_all(
            ".msg.assistant .bubble", "els => els[els.length-1].textContent")
        checks.append(("non-stream reply non-empty", len(reply2.strip()) > 3, reply2[:70]))

        # 6) 前端并发压测按钮
        page.click("#benchBtn")
        page.wait_for_function(
            "() => document.getElementById('benchOut').textContent.includes('ok=')",
            timeout=args.timeout,
        )
        bench = page.inner_text("#benchOut")
        ok = "fail=0" in bench
        checks.append(("frontend bench fail=0", ok, bench[:120]))

        page.screenshot(path=args.out, full_page=False)
        browser.close()

    print()
    failed = 0
    for name, good, detail in checks:
        print(f"[{'PASS' if good else 'FAIL'}] {name}  ({detail})")
        if not good:
            failed += 1
    print(f"\n{len(checks) - failed}/{len(checks)} passed · screenshot -> {args.out}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
