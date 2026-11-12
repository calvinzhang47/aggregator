#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""聚合结果后处理：去重 → 校验 → 重新生成三种订阅格式。

设计要点
--------
1. 去重：按 (type, server, port, 凭据, 传输层关键字段) 生成指纹，
   同一节点在多个源里出现时只保留一个，并顺带把名字里重复的 " #n" 去掉。
2. 校验：去重后节点数低于 MIN_NODES 视为本次失败，退出码 1。
   这时工作流不会更新 Gist，上一次的订阅内容原样保留。
3. 兜底：如果本次失败但存在上一次成功的结果（.previous/output），
   直接把上一次的结果复制回来发布，保证订阅地址始终可用。
4. 重建格式：以去重后的 Clash YAML 为唯一数据源，调用仓库自带的
   subconverter 生成 sing-box 与 Shadowrocket 可用的 base64 订阅，
   保证三种格式的节点集合一致（anytls 这类 subconverter 无法转成 URI
   的协议只保留在 Clash 格式中）。
"""

from __future__ import annotations

import base64
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTPUT = os.path.join(ROOT, "output")
PREVIOUS = os.path.join(ROOT, ".previous", "output")
SUBCONVERTER = os.path.join(ROOT, "subconverter")

CLASH_FILE = os.path.join(OUTPUT, "proxy-clash.yaml")
SINGBOX_FILE = os.path.join(OUTPUT, "proxy-singbox.json")
V2RAY_FILE = os.path.join(OUTPUT, "proxy-v2ray.txt")

MIN_NODES = int(os.environ.get("MIN_NODES", "5"))

CRED_KEYS = ("uuid", "password", "psk", "auth", "auth-str", "private-key", "public-key")
TRANSPORT_KEYS = ("network", "servername", "sni", "grpc-service-name", "ws-path", "h2-path", "flow")


def log(msg: str) -> None:
    print(msg, flush=True)


def fingerprint(proxy: dict) -> str:
    """节点指纹：协议 + 服务器 + 端口 + 凭据 + 传输特征。"""
    parts = [str(proxy.get("type", "")).lower(), str(proxy.get("server", "")).lower(), str(proxy.get("port", ""))]
    for key in CRED_KEYS:
        value = proxy.get(key)
        if value:
            parts.append(f"{key}={value}")
    for key in TRANSPORT_KEYS:
        value = proxy.get(key)
        if value:
            parts.append(f"{key}={value}")
    opts = proxy.get("plugin-opts") or proxy.get("reality-opts") or {}
    if isinstance(opts, dict):
        for key in sorted(opts):
            if key in ("public-key", "short-id", "mode", "host"):
                parts.append(f"{key}={opts[key]}")
    return "|".join(parts)


def load_proxies() -> list[dict]:
    if not os.path.isfile(CLASH_FILE):
        return []
    try:
        with io.open(CLASH_FILE, encoding="utf-8") as reader:
            data = yaml.safe_load(reader) or {}
    except Exception as exc:  # noqa: BLE001
        log(f"[warn] 解析 {CLASH_FILE} 失败: {exc}")
        return []
    proxies = data.get("proxies") or []
    return [p for p in proxies if isinstance(p, dict) and p.get("server") and p.get("port")]


def dedupe(proxies: list[dict]) -> list[dict]:
    seen, kept = set(), []
    for proxy in proxies:
        key = fingerprint(proxy)
        if key in seen:
            continue
        seen.add(key)
        name = str(proxy.get("name", "")).strip()
        # 去掉 subconverter 自动追加的 " #1" / " #2" 后缀
        proxy["name"] = re.sub(r"\s+#\d+$", "", name) or "node"
        kept.append(proxy)

    # 名字去重，避免客户端因重名报错
    counter: dict[str, int] = {}
    for proxy in kept:
        name = proxy["name"]
        if name in counter:
            counter[name] += 1
            proxy["name"] = f"{name} {counter[name]}"
        else:
            counter[name] = 1
    return kept


def write_clash(proxies: list[dict]) -> None:
    data = {"proxies": proxies}
    with io.open(CLASH_FILE, "w", encoding="utf-8") as writer:
        yaml.dump(data, writer, allow_unicode=True, sort_keys=False, default_flow_style=False)


def subconverter_binary() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "windows":
        name = "subconverter-windows-amd.exe"
    elif system == "darwin":
        name = "subconverter-darwin-arm" if "arm" in machine else "subconverter-darwin-amd"
    else:
        name = "subconverter-linux-arm" if ("arm" in machine or "aarch64" in machine) else "subconverter-linux-amd"
    path = os.path.join(SUBCONVERTER, name)
    if os.path.isfile(path):
        try:
            os.chmod(path, 0o755)
        except OSError:
            pass
    return path


def convert(target: str, dest: str) -> str:
    """用 subconverter 把 output/proxy-clash.yaml 转成指定格式，返回文本内容。"""
    binary = subconverter_binary()
    if not os.path.isfile(binary):
        log(f"[warn] 未找到 subconverter 二进制: {binary}")
        return ""

    artifact = f"post_{target}"
    ini_path = os.path.join(SUBCONVERTER, "generate.ini")
    out_path = os.path.join(SUBCONVERTER, dest)
    if os.path.exists(out_path):
        os.remove(out_path)

    # subconverter 要求源文件路径相对于其工作目录
    shutil.copyfile(CLASH_FILE, os.path.join(SUBCONVERTER, "post_src.yaml"))
    lines = [
        f"[{artifact}]",
        f"path={dest}",
        "url=post_src.yaml",
        "expand=false",
        f"target={target}",
        "list=true",
        "emoji=true",
        "add_emoji=true",
        "",
    ]
    with io.open(ini_path, "w", encoding="utf-8") as writer:
        writer.write("\n".join(lines))

    try:
        subprocess.run(
            [binary, "-g", "--artifact", artifact],
            cwd=SUBCONVERTER,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=180,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        log(f"[warn] 转换 {target} 异常: {exc}")
        return ""

    if not os.path.isfile(out_path):
        log(f"[warn] 转换 {target} 没有产生文件")
        return ""

    with io.open(out_path, encoding="utf-8", errors="replace") as reader:
        return reader.read().strip()


def restore_previous() -> bool:
    if not os.path.isdir(PREVIOUS):
        log("[fail] 没有任何历史结果可沿用")
        return False
    os.makedirs(OUTPUT, exist_ok=True)
    for name in os.listdir(PREVIOUS):
        shutil.copy2(os.path.join(PREVIOUS, name), os.path.join(OUTPUT, name))
    log(f"[fallback] 已沿用上一次成功结果: {sorted(os.listdir(OUTPUT))}")
    return True


def main() -> int:
    proxies = load_proxies()
    log(f"[1/4] 本次抓取并验活后节点数: {len(proxies)}")

    proxies = dedupe(proxies)
    log(f"[2/4] 去重后节点数: {len(proxies)}")

    if len(proxies) < MIN_NODES:
        log(f"[fail] 去重后仅 {len(proxies)} 个节点，低于阈值 {MIN_NODES}，判定本次失败")
        if restore_previous():
            log("[fallback] 本次不更新订阅内容，沿用上一次结果")
            return 0
        return 1

    write_clash(proxies)
    log(f"[3/4] 已写回去重后的 Clash 配置: {CLASH_FILE}")

    singbox = convert("singbox", "proxy-singbox.json")
    if not singbox:
        log("[warn] sing-box 转换失败，保留原文件")
    else:
        with io.open(SINGBOX_FILE, "w", encoding="utf-8") as writer:
            writer.write(singbox)

    mixed = convert("mixed", "proxy-v2ray.txt")
    if not mixed:
        log("[warn] base64 订阅转换失败，保留原文件")
    else:
        # Shadowrocket 期望 base64；subconverter 的 mixed 在 list 模式下输出的是明文 URI 列表
        if "://" in mixed:
            mixed = base64.b64encode(mixed.encode("utf-8")).decode("ascii")
        with io.open(V2RAY_FILE, "w", encoding="utf-8") as writer:
            writer.write(mixed)

    v2ray_lines = 0
    if os.path.isfile(V2RAY_FILE):
        raw = io.open(V2RAY_FILE, encoding="utf-8").read().strip()
        try:
            v2ray_lines = len([x for x in base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8").splitlines() if "://" in x])
        except Exception:  # noqa: BLE001
            v2ray_lines = 0

    log(f"[4/4] 产物: clash={len(proxies)} 节点 / singbox={os.path.getsize(SINGBOX_FILE) if os.path.exists(SINGBOX_FILE) else 0}B / base64={v2ray_lines} 节点")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with io.open(summary, "a", encoding="utf-8") as writer:
            writer.write("### 本次聚合结果\n\n")
            writer.write(f"- 验活后节点：**{len(proxies)}**\n")
            writer.write(f"- base64 订阅（Shadowrocket）：**{v2ray_lines}** 节点\n")
            writer.write(f"- 协议分布：`{json.dumps(_protocol_stats(proxies), ensure_ascii=False)}`\n")

    return 0


def _protocol_stats(proxies: list[dict]) -> dict:
    stats: dict[str, int] = {}
    for proxy in proxies:
        key = str(proxy.get("type", "unknown"))
        stats[key] = stats.get(key, 0) + 1
    return stats


if __name__ == "__main__":
    sys.exit(main())
