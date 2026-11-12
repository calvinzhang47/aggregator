#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把聚合产物写入 GitHub Gist，并输出可直接导入客户端的订阅地址。

需要的环境变量
--------------
GIST_TOKEN : 具备 gist 权限的 GitHub Token（在仓库 Secrets 里配置，绝不写进代码）
GIST_ID    : 目标 Gist 的 ID；留空则自动新建一个 secret Gist
GIST_USER  : 可选，Gist 所属用户名；不填则从 /user 接口推断
"""

from __future__ import annotations

import io
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTPUT = os.path.join(ROOT, "output")

# Gist 里的文件名 -> 本地文件
FILES = {
    "clash.yaml": "proxy-clash.yaml",
    "singbox.json": "proxy-singbox.json",
    "shadowrocket.txt": "proxy-v2ray.txt",
}

API = "https://api.github.com"


def log(message: str) -> None:
    print(message, flush=True)


def request(method: str, url: str, token: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "aggregator-ci")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def collect() -> dict:
    files = {}
    for remote_name, local_name in FILES.items():
        path = os.path.join(OUTPUT, local_name)
        if not os.path.isfile(path):
            log(f"[skip] 缺少 {local_name}")
            continue
        with io.open(path, encoding="utf-8") as reader:
            files[remote_name] = {"content": reader.read()}
    return files


def main() -> int:
    token = (os.environ.get("GIST_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    gist_id = (os.environ.get("GIST_ID") or "").strip()
    user = (os.environ.get("GIST_USER") or "").strip()

    if not token:
        log("[fail] 未配置 GIST_TOKEN，无法写入 Gist")
        return 1

    files = collect()
    if not files:
        log("[fail] output/ 下没有任何可发布的订阅文件")
        return 1

    if not user:
        try:
            user = request("GET", f"{API}/user", token).get("login", "")
        except Exception as exc:  # noqa: BLE001
            log(f"[warn] 查询用户名失败: {exc}")

    try:
        if gist_id:
            result = request("PATCH", f"{API}/gists/{gist_id}", token, {"files": files})
            log(f"[ok] 已更新 Gist {gist_id}")
        else:
            payload = {
                "description": "免费节点订阅（自动更新，来源为公开仓库）",
                "public": False,
                "files": files,
            }
            result = request("POST", f"{API}/gists", token, payload)
            gist_id = result.get("id", "")
            user = (result.get("owner") or {}).get("login", user)
            log(f"[ok] 已新建 Gist {gist_id}（请把它加入仓库 Secrets: GIST_ID）")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        log(f"[fail] Gist 写入失败 {exc.code}: {detail}")
        return 1

    base = f"https://gist.githubusercontent.com/{user}/{gist_id}/raw"
    urls = {name: f"{base}/{name}" for name in files}

    log("")
    log("订阅地址（直接填进客户端）：")
    for name, url in urls.items():
        log(f"  {name:18s} {url}")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with io.open(summary, "a", encoding="utf-8") as writer:
            writer.write("\n### 订阅地址\n\n")
            writer.write("| 客户端 | 地址 |\n|---|---|\n")
            for name, url in urls.items():
                writer.write(f"| {name} | `{url}` |\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
