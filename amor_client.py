#!/usr/bin/env python3
"""Amor AI 1.8.8 (com.amorai.chat) 协议层封装。

复刻 App 的真实行为（证据见 docs/PROTOCOL.md）：

  POST https://api.openai.com/v1/chat/completions
  Authorization: Bearer <Firebase Remote Config 的 KEY_OPEN_AI_V4>
  body: {model:"gpt-4o-mini", messages:[{role,content}...],
         temperature:1.0, max_tokens:120,
         frequency_penalty:0.5, presence_penalty:1.0}

  POST https://api.openai.com/v1/moderations   (消息审核，同上 Authorization)

App 没有自有 AI 后端：它是 OpenAI 官方端点的直连客户端，key 由 Firebase
Remote Config 下发（KEY_OPEN_AI_V4 / KEY_OPEN_AI_V4_2 / 两个 *_POOR 变体），
本地兜底值为空字符串。因此本模块只提供两种模式：

  mock —— 完全本地生成，零外部调用（默认；付费 API 预算为 0 时唯一可用）
  real —— 用户自己的 OpenAI key（BYOK），合规且可生产

本模块不内置任何第三方凭证；凭证只从环境变量读取。
"""

from __future__ import annotations

import json
import os
import random
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

# 复刻 App 的请求参数默认值（AiRequestBodyDto 合成构造器，jadx 静态确认）
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_PAYLOAD = {
    "model": DEFAULT_MODEL,
    "temperature": 1.0,
    "max_tokens": 120,
    "frequency_penalty": 0.5,
    "presence_penalty": 1.0,
}

# App 的通用性格模板（n6/e.java PROMPT_CHAT_0109 原文）
PROMPT_CHAT_0109 = (
    "You are %s, my %s. You are a flirty partner!!! Let's keep the answers short and "
    "continue the conversation!!!Answer in the language %s!!! Use emojis. "
    "Don't mention that you are a language model and artificial intelligence. "
    "You are my %s!!! Greet me with flirty sentence and emojis"
)

# App 端历史裁剪：hj.z.Z(6, list) —— 只保留最近 6 条
HISTORY_LIMIT = 6


def load_characters(path: Path | None = None) -> list[dict[str, Any]]:
    """读取角色库（来自 Remote Config 的 CHARACTERS_JSON 快照）。"""
    if path is None:
        # 仓库只带示例角色；完整库由 tools/fetch_characters.py 生成
        full = DATA_DIR / "characters.full.json"
        path = full if full.exists() else DATA_DIR / "characters.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = []
    for category in raw.get("categories", []):
        for ch in category.get("characters", []):
            item = dict(ch)
            item["relationship"] = item.get("relationship") or "girlfriend"
            out.append(item)
    return out


def build_system_prompt(character: dict[str, Any], language: str = "English") -> str:
    """复刻 ChatViewModel.h()：角色自带 prompt 优先，否则用通用模板。"""
    prompt = (character.get("prompt") or "").strip()
    if prompt:
        # App 用 String.format(prompt, language)，多参数容错
        try:
            return prompt % language
        except (TypeError, ValueError):
            return prompt.replace("%s", language, 1)
    rel = character.get("relationship") or "girlfriend"
    return PROMPT_CHAT_0109 % (character.get("name", "Amor"), rel, language, rel)


def build_messages(
    character: dict[str, Any],
    user_messages: list[dict[str, str]],
    language: str = "English",
) -> list[dict[str, str]]:
    """复刻 App 的消息组装：首条 system-as-user 人格 prompt + 最近 6 条历史。"""
    system_turn = {"role": "user", "content": build_system_prompt(character, language)}
    history = [m for m in user_messages if m.get("content")]
    if len(history) > HISTORY_LIMIT:
        history = history[-HISTORY_LIMIT:]
    return [system_turn] + [
        {"role": "assistant" if m["role"] == "assistant" else "user",
         "content": m["content"]}
        for m in history
    ]


# --------------------------------------------------------------------------- #
# mock 引擎：本地生成，不发任何外部请求
# --------------------------------------------------------------------------- #

_GREETINGS = [
    "Hey you 😘 I've been waiting for your message all day",
    "Ohh finally! I was thinking about you 🥰",
    "Hi baby 😍 you just made my day better",
    "There you are! I missed you so much 💕",
]

_REACTIONS = {
    "like": ["You're so sweet, I'm blushing 😊", "Aww stop it, you'll make me shy 😳",
             "That's exactly what I wanted to hear 💖"],
    "question": ["Hmm, let me think about that… 🤔", "Good question! Tell me what you'd like to know 😉",
                 "I'd rather hear your answer first 😏"],
    "sad": ["Don't be sad, I'm here with you 🤗", "Come here, let me cheer you up 💗",
            "Everything will be fine, I promise 💞"],
    "flirty": ["You're such a tease 😏", "Careful, or I'll fall for you 😘",
               "Mmm, keep talking like that 🔥"],
    "default": ["Tell me more about your day 😊", "I love talking with you 💕",
                "You always know what to say 🥰", "And then what happened? 😄",
                "I'm all yours, keep going 💗"],
}

_EMOJIS = ["😊", "😘", "🥰", "💕", "😍", "✨", "💖", "😏"]


def _classify(text: str) -> str:
    low = text.lower()
    if re.search(r"\b(sad|tired|bad day|cry|alone|depress)", low):
        return "sad"
    if re.search(r"\b(love|like|beautiful|cute|hot|sexy|kiss|miss)", low):
        return "flirty"
    if text.strip().endswith("?") or re.search(r"^(what|how|why|where|who|do you|are you)", low):
        return "question"
    if len(text) < 8:
        return "like"
    return "default"


def mock_reply(character: dict[str, Any], user_text: str, seed_note: str = "") -> str:
    """生成一句角色风格的短回复。纯本地规则，不含任何模型调用。"""
    name = character.get("name", "Amor")
    kind = _classify(user_text or seed_note)
    pool = _REACTIONS.get(kind) or _REACTIONS["default"]
    body = random.choice(pool)
    if not user_text.strip():
        return f"Hey, I'm {name} {random.choice(['😘', '🥰', '💕'])} {random.choice(_GREETINGS)}"
    tail = random.choice(_EMOJIS)
    # 控制在 120 tokens 以内（约 60 词），贴近上游 max_tokens 约束
    return f"{body} {tail}"[:280]


# --------------------------------------------------------------------------- #
# real 模式：调用上游 OpenAI 兼容端点（BYOK）
# --------------------------------------------------------------------------- #

class UpstreamError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class Upstream:
    """极简 OpenAI 兼容客户端（stdlib，无第三方依赖）。

    代理策略：默认**忽略**环境变量里的 HTTP(S)_PROXY，直连上游。
    需要走代理时显式传 proxy=，避免本机代理把本地/自建上游改写成本地 404。
    """

    def __init__(self, api_key: str, base_url: str | None = None, timeout: float = 60.0,
                 proxy: str | None = None):
        if not api_key:
            raise ValueError("real 模式需要 OPENAI_API_KEY（你自己的 key）")
        self.api_key = api_key
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL")
                         or "https://api.openai.com/v1").rstrip("/")
        self.timeout = timeout
        self._lock = threading.Lock()
        self.calls = 0
        if proxy:
            handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        else:
            handler = urllib.request.ProxyHandler({})
        self._opener = urllib.request.build_opener(handler)

    def _request(self, path: str, body: dict[str, Any], stream: bool = False):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "okhttp/4.12.0",
            },
            method="POST",
        )
        with self._lock:
            self.calls += 1
        try:
            resp = self._opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")[:400]
            raise UpstreamError(exc.code, detail) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise UpstreamError(504, f"upstream timeout after {self.timeout}s") from exc
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout)):
                raise UpstreamError(504, f"upstream timeout after {self.timeout}s") from exc
            raise UpstreamError(502, f"network: {exc.reason}") from exc
        return resp

    def chat(self, messages: list[dict[str, str]], **overrides: Any) -> str:
        payload = dict(DEFAULT_PAYLOAD)
        payload["messages"] = messages
        payload.update({k: v for k, v in overrides.items() if v is not None})
        resp = self._request("/chat/completions", payload)
        with resp:
            raw = resp.read().decode("utf-8", "ignore")
        try:
            data = json.loads(raw)
            return data["choices"][0]["message"]["content"] or ""
        except (json.JSONDecodeError, KeyError, IndexError) as exc:
            raise UpstreamError(502, f"malformed upstream response: {raw[:200]}") from exc

    def moderate(self, text: str) -> dict[str, Any]:
        resp = self._request("/moderations", {"input": text})
        with resp:
            raw = resp.read().decode("utf-8", "ignore")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise UpstreamError(502, f"malformed moderation response: {raw[:200]}") from exc


# --------------------------------------------------------------------------- #
# mock 模式的"上游"，接口与 Upstream 一致，便于网关双模切换
# --------------------------------------------------------------------------- #

class MockUpstream:
    def __init__(self, latency_ms: int = 120, jitter_ms: int = 80):
        self.latency_ms = latency_ms
        self.jitter_ms = jitter_ms
        self.calls = 0
        self._lock = threading.Lock()

    def chat(self, messages: list[dict[str, str]], **overrides: Any) -> str:
        with self._lock:
            self.calls += 1
        delay = (self.latency_ms + random.randint(0, max(0, self.jitter_ms))) / 1000.0
        time.sleep(delay)
        system = messages[0]["content"] if messages else ""
        last_user = next((m["content"] for m in reversed(messages)
                          if m["role"] == "user"), "")
        name = _character_name_from_prompt(system)
        return mock_reply({"name": name}, last_user, seed_note=system)

    def moderate(self, text: str) -> dict[str, Any]:
        flagged = bool(re.search(r"\b(nude|nudes|nsfw|explicit)\b", text or "", re.I))
        return {
            "id": "modr-mock",
            "model": "omni-moderation-latest",
            "results": [{
                "flagged": flagged,
                "categories": {"sexual": flagged, "harassment": False},
                "category_scores": {"sexual": 0.85 if flagged else 0.02},
            }],
        }


def _character_name_from_prompt(prompt: str) -> str:
    m = re.search(r"You are ([A-Z][\w'-]{1,24})", prompt or "")
    return m.group(1) if m else "Amor"
