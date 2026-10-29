#!/usr/bin/env python3
"""从 Firebase Remote Config 拉取 Amor AI 的角色库与配置快照。

App 自己就是这么拉的：Remote Config 是公开 REST（只需项目 API key，无需登录）。
本脚本只取**非凭证**部分：角色库 + 行为参数。所有 KEY_OPEN_AI* 一律剔除。

    python tools/fetch_characters.py                     # 输出到 data/
    python tools/fetch_characters.py --out data/full.json

仓库里只带了 3 个示例角色（版权考虑）；完整 102 个角色用本脚本自行生成。
"""

from __future__ import annotations

import argparse
import json
import urllib.request
import uuid
from pathlib import Path

DEFAULT_PROJECT = "amor-ai"
DEFAULT_API_KEY = "AIzaSyDVI80g3-6T9UUSrZ7VXPoBhHBgEeqbtSY"  # Firebase 公开项目标识
DEFAULT_APP_ID = "1:487532596722:android:08af1fd6fe2026e54caf6b"
DEFAULT_PACKAGE = "com.amorai.chat"


def fetch(project: str, api_key: str, app_id: str, package: str, version: str,
          proxy: str | None) -> dict:
    body = {
        "app_instance_id": str(uuid.uuid4()),
        "app_id": app_id,
        "country_code": "US",
        "language_code": "en",
        "platform_version": "34",
        "package_name": package,
        "app_version": version,
        "sdk_version": "21.1.1",
        "time_zone": "UTC",
    }
    url = (f"https://firebaseremoteconfig.googleapis.com/v1/projects/{project}"
           f"/namespaces/firebase:fetch?key={api_key}")
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    handler = (urllib.request.ProxyHandler({"http": proxy, "https": proxy})
               if proxy else urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(handler)
    with opener.open(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=DEFAULT_PROJECT)
    ap.add_argument("--api-key", default=DEFAULT_API_KEY)
    ap.add_argument("--app-id", default=DEFAULT_APP_ID)
    ap.add_argument("--package", default=DEFAULT_PACKAGE)
    ap.add_argument("--version", default="1.8.8")
    ap.add_argument("--proxy", default=None, help="如 http://127.0.0.1:10808；默认直连")
    ap.add_argument("--out", default="data/characters.full.json")
    args = ap.parse_args()

    rc = fetch(args.project, args.api_key, args.app_id, args.package, args.version, args.proxy)
    entries = rc.get("entries") or {}
    print(f"templateVersion={rc.get('templateVersion')} params={len(entries)}")

    raw = entries.get("CHARACTERS_JSON")
    if not raw:
        raise SystemExit("CHARACTERS_JSON 缺失，检查 project/api-key/app-id")

    chars = json.loads(raw)
    out = {"version": chars.get("version"),
           "templateVersion": rc.get("templateVersion"), "categories": []}
    for cat in chars.get("list", []):
        items = []
        for ch in cat.get("characters", []):
            desc = ch.get("description")
            items.append({
                "id": ch["id"], "name": ch.get("name"), "age": ch.get("age"),
                "role": ch.get("role"), "gender": cat.get("category"),
                "baseUrl": ch.get("baseUrl"), "tags": ch.get("tags"),
                "prompt": ch.get("prompt"), "is_premium": ch.get("is_premium", False),
                "description": desc[0] if isinstance(desc, list) and desc else desc,
            })
        out["categories"].append({"category": cat.get("category"), "characters": items})

    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(len(c["characters"]) for c in out["categories"])
    print(f"characters={total} -> {path}")

    # 行为参数快照（剔除全部凭证）
    safe = {k: v for k, v in entries.items()
            if not k.startswith("KEY_OPEN_AI") and k != "CHARACTERS_JSON"}
    snap = Path("data/remote_config.json")
    snap.write_text(json.dumps(safe, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"config params={len(safe)} -> {snap}  (已剔除所有 KEY_OPEN_AI*)")


if __name__ == "__main__":
    main()
