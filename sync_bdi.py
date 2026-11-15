#!/usr/bin/env python3
"""
sync_bdi.py — 把解析好的 BDI JSON 同步到 navgreen API

接口（通过 SYNC_TOKEN 环境变量提供 token 请求头）：
    POST https://miniapi.navgreen.cn/api/data/baltic_exchange_index/sync
请求体即 output/json/bdi_<date>.json 的原样内容（字段逐一对应，无需映射）。

用法：
    python sync_bdi.py --date 2026-05-20            # 同步指定日期
    python sync_bdi.py --last 3                     # 同步最近 3 个「完整」JSON（默认）
    python sync_bdi.py --all                        # 同步全部 JSON
    python sync_bdi.py --last 5 --allow-incomplete  # 连未完成（BDI 为空）的也推

接口地址可用环境变量 SYNC_URL 覆盖。HTTP 和业务 code 均成功才算同步成功。
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests

HERE     = Path(__file__).resolve().parent
JSON_DIR = HERE / "output" / "json"
# 用 `or` 兜底：GitHub Actions 把未设置的 secret 展开成空串，空串需回退到默认值
SYNC_URL = os.getenv("SYNC_URL") or \
    "https://miniapi.navgreen.cn/api/data/baltic_exchange_index/sync"
SYNC_TOKEN = (os.getenv("SYNC_TOKEN") or "").strip()

JSON_RE = re.compile(r"^bdi_(\d{4}-\d{2}-\d{2})\.json$")


def list_json_files():
    """Return [(date_str, Path), ...] sorted ascending by date."""
    out = []
    if not JSON_DIR.exists():
        return out
    for p in sorted(JSON_DIR.iterdir()):
        m = JSON_RE.match(p.name)
        if m:
            out.append((m.group(1), p))
    return out


def is_complete(payload: dict) -> bool:
    """完整 = 干散总指数 BDI 已到齐（BRS Daily 滞后字段不为空）。"""
    return payload.get("BDI") not in (None, "")


def post_one(date_str: str, payload: dict) -> bool:
    if not SYNC_TOKEN:
        print(f"  [ERR]  {date_str}  缺少 SYNC_TOKEN，请配置有同步权限的有效 token")
        return False
    try:
        resp = requests.post(
            SYNC_URL,
            headers={"accept": "application/json",
                     "Content-Type": "application/json",
                     "token": SYNC_TOKEN},
            json=payload,
            timeout=30,
            allow_redirects=False,
        )
    except requests.RequestException as e:
        print(f"  [ERR]  {date_str}  请求异常: {type(e).__name__}")
        return False

    try:
        body = resp.json()
    except ValueError:
        print(f"  [ERR]  {date_str}  HTTP {resp.status_code}  接口未返回有效 JSON")
        return False
    code = body.get("code") if isinstance(body, dict) else None
    message = str(body.get("msg") or body.get("message") or "") if isinstance(body, dict) else ""
    # 业务错误同样可能返回 HTTP 200；不能将 token 过期或权限不足计为成功。
    ok = 200 <= resp.status_code < 300 and code == 200
    tag = "OK " if ok else "ERR"
    message = message.replace(SYNC_TOKEN, "[REDACTED]")[:200]
    print(f"  [{tag}] {date_str}  HTTP {resp.status_code}  code={code}  {message}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--date", type=str, help="只同步指定日期 YYYY-MM-DD")
    g.add_argument("--last", type=int, help="同步最近 N 个 JSON（默认 3）")
    g.add_argument("--all",  action="store_true", help="同步全部 JSON")
    ap.add_argument("--allow-incomplete", action="store_true",
                    help="连 BDI 为空的未完成记录也推送")
    args = ap.parse_args()

    if not SYNC_TOKEN:
        print("[sync] 缺少 SYNC_TOKEN，请在环境变量或 GitHub Actions Secrets 中配置有效 token")
        sys.exit(1)

    files = list_json_files()
    if not files:
        print(f"[sync] {JSON_DIR} 下没有 bdi_*.json，无可同步内容")
        sys.exit(1)

    if args.date:
        selected = [(d, p) for d, p in files if d == args.date]
        if not selected:
            print(f"[sync] 找不到 output/json/bdi_{args.date}.json")
            sys.exit(1)
    elif args.all:
        selected = files
    else:
        n = args.last if args.last else 3
        selected = files[-n:]

    print(f"[sync] 目标接口: {SYNC_URL}")
    print(f"[sync] 候选 {len(selected)} 个日期")

    posted, skipped, failed = [], [], []
    for date_str, path in selected:
        with path.open(encoding="utf-8") as source:
            payload = json.load(source)
        if not args.allow_incomplete and not is_complete(payload):
            print(f"  [SKIP] {date_str}  BDI 未到齐（滞后未完成）—— 次日重跑会自动补推")
            skipped.append(date_str)
            continue
        if post_one(date_str, payload):
            posted.append(date_str)
        else:
            failed.append(date_str)

    print(f"\n[sync] 完成。成功 {len(posted)}，跳过 {len(skipped)}，失败 {len(failed)}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
