#!/usr/bin/env python3
"""
run_bdi.py — Aquabridge BDI 一键抓取 + 解析 + 入库 (nora@reachyint.com 源)

主线分支使用 nora@reachyint.com 转发的 Baltic Exchange 官方 PDF 包，
一封邮件包含全部 6~7 份 PDF（Baltic Dry/Capesize/Panamax/Supramax/Handysize Index
+ Dry TC Averages + Fixtures Report），主题如:
    "FW: Baltic Index Reports& Fixture Report 23.4.2026"

如果 nora 中断且短期内不恢复，切到 brs-source 分支即可使用 BRS Brokers 源。

每天运行一次:
    python3 run_bdi.py                                # 抓最近 7 天
    python3 run_bdi.py --days 14                      # 回溯 14 天
    python3 run_bdi.py --date 2026-04-23              # 只处理指定日期
    python3 run_bdi.py --date 2026-04-23 --force      # 即使 CSV 已有也覆盖

输出:
    1) 解析后的 JSON → ./output/json/bdi_YYYY-MM-DD.json
    2) 附件备份 → ./output/attachments/YYYY-MM-DD/
    3) Append/覆盖一行到 ./baltic_exchange_index_history.csv

依赖:
    .venv/bin/pip install pdfplumber certifi
"""
import argparse
import csv
import email
import imaplib
import json
import re
import ssl
import tempfile
import zipfile
from datetime import datetime, timedelta
from email.header import decode_header
from pathlib import Path

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
IMAP_HOST = "imap.qiye.aliyun.com"
IMAP_PORT = 993
IMAP_USER = "data@aquabridge.ai"
IMAP_PWD  = "bZ4NqiA4k8WK6iIb"

# 监听的发件人（含 Reach Shipping 转发链路上的所有可能 From）
TARGET_SENDERS = [
    "nora@reachyint.com",
    "reachyint.com",
]

# 邮件主题正则 — 兼容历史的 "Baltic Index Reports& Fixture Report 23.4.2026" 等格式
SUBJECT_RE = re.compile(r"Baltic\s+Index\s+Reports.*Fixture\s+Report", re.IGNORECASE)

HERE = Path(__file__).resolve().parent
OUTPUT_DIR  = HERE / "output"
JSON_DIR    = OUTPUT_DIR / "json"
ATTACH_DIR  = OUTPUT_DIR / "attachments"
CSV_PATH    = HERE / "baltic_exchange_index_history.csv"

CSV_COLUMNS = [
    "_id","date","BCI","BDI","BHSI","BPI","BSI",
    "C10_182","C14_182","C16_182","C17","C17-TCE","C2","C2-TCE","C3","C3-TCE",
    "C5","C5-TCE","C5TC","C5TC (182)","C7","C7-TCE","C8_182","C9_182",
    "HS1_38","HS2_38","HS3_38","HS4_38","HS5_38","HS6_38","HS7TC","HS7_38",
    "P1A_82","P2A_82","P3A_82","P4_82","P5TC","P5_82","P6_82",
    "P7","P7-TCE","P8","P8-TCE",
    "S10","S10TC","S11TC","S15","S1B","S1C","S2","S3","S3TC_63",
    "S4A","S4B","S5","S6TC","S8","S9","updated_at",
    "FFADVCape_T","FFADVHS_T","FFADVPmx_T","FFADVSupr_T",
    "FFADV_C3","FFADV_C5","FFADV_C5TC","FFADV_C7","FFADV_H6TC","FFADV_H7TC",
    "FFADV_P2","FFADV_P3","FFADV_P4TC","FFADV_P5TC","FFADV_P6","FFADV_P8",
    "FFADV_S10TC","FFADV_S11TC","FFADV_S6TC","FFADV_T",
    "C19-TCE","P4TC","C10","C14","C16","C8","C9",
]


# ---------------------------------------------------------------------------
# IMAP helpers
# ---------------------------------------------------------------------------
def decode_str(s):
    if s is None: return ""
    out = []
    for text, enc in decode_header(s):
        if isinstance(text, bytes):
            try:
                out.append(text.decode(enc or "utf-8", errors="replace"))
            except Exception:
                out.append(text.decode("utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def login():
    print(f"  ↪ Login {IMAP_USER}@{IMAP_HOST}:{IMAP_PORT}")
    ctx = ssl.create_default_context()
    M = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ctx)
    M.login(IMAP_USER, IMAP_PWD)
    M.select("INBOX", readonly=True)
    return M


def fetch_bdi_mails(M, since_days=7):
    """Find nora@reachyint.com BDI report mails in the last N days.

    Aliyun IMAP 不支持 FROM 子串搜索：服务器只用 SINCE 过滤，发件人在客户端筛。
    """
    since = (datetime.now() - timedelta(days=since_days)).strftime("%d-%b-%Y")
    typ, ids = M.search(None, "SINCE", since)
    if typ != "OK" or not ids or not ids[0]:
        return []
    senders_lc = [s.lower() for s in TARGET_SENDERS]
    found = []
    seen = set()
    for mid in ids[0].split():
        typ, data = M.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT DATE FROM)])")
        raw = data[0][1] if data and data[0] else b""
        msg = email.message_from_bytes(raw)
        fr = (decode_str(msg.get("From")) or "").lower()
        if not any(s in fr for s in senders_lc):
            continue
        subj = decode_str(msg.get("Subject")) or ""
        if not SUBJECT_RE.search(subj):
            continue
        if mid in seen:
            continue
        seen.add(mid)
        found.append((mid, subj))
    return found


def download_attachments(M, mid, dest_dir: Path):
    """Download PDF/zip attachments of a mail to dest_dir."""
    typ, data = M.fetch(mid, "(RFC822)")
    if typ != "OK": return []
    msg = email.message_from_bytes(data[0][1])
    saved = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart": continue
        fname = part.get_filename()
        if not fname: continue
        fname = decode_str(fname)
        if not (fname.lower().endswith(".pdf") or fname.lower().endswith(".zip")):
            continue
        target = dest_dir / fname
        payload = part.get_payload(decode=True)
        if not payload: continue
        with open(target, "wb") as f:
            f.write(payload)
        saved.append(target)
    return saved


# ---------------------------------------------------------------------------
# CSV append
# ---------------------------------------------------------------------------
def ensure_csv():
    if not CSV_PATH.exists():
        with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(CSV_COLUMNS)


def append_row(parsed: dict):
    ensure_csv()
    row = []
    for col in CSV_COLUMNS:
        if col == "_id":
            row.append("")
        elif col == "updated_at":
            row.append(parsed.get("updated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        else:
            v = parsed.get(col, "")
            row.append("" if v is None else v)
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)


def existing_csv_dates() -> set:
    if not CSV_PATH.exists():
        return set()
    out = set()
    with open(CSV_PATH, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            d = (row.get("date") or "").strip()
            if d: out.add(d)
    return out


def remove_date_row(date_str: str):
    if not CSV_PATH.exists():
        return
    with open(CSV_PATH, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = [r for r in reader if r.get("date") != date_str]
        fields = reader.fieldnames
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days",  type=int, default=7,  help="回溯天数 (default 7)")
    ap.add_argument("--date",  type=str, default=None, help="只处理指定日期 YYYY-MM-DD")
    ap.add_argument("--force", action="store_true",   help="即使 CSV 已有该日期也重新写入（先删除旧行）")
    args = ap.parse_args()

    JSON_DIR.mkdir(parents=True, exist_ok=True)
    ATTACH_DIR.mkdir(parents=True, exist_ok=True)
    from parse_bdi import parse_day

    print("[1/3] 连接邮箱并搜索 nora@reachyint.com BDI 报告邮件 …")
    M = login()
    mails = fetch_bdi_mails(M, since_days=args.days)
    print(f"      找到 {len(mails)} 封匹配邮件")
    for mid, subj in mails:
        print(f"        [{mid.decode()}] {subj}")

    print(f"\n[2/3] 下载附件并解析 …")
    existing = existing_csv_dates()
    success_dates = []
    written_this_run = set()
    for mid, subj in mails:
        m = re.search(r"(\d{1,2})[\.\-/](\d{1,2})[\.\-/](\d{4})", subj)
        date_hint = None
        if m:
            d, mo, y = m.groups()
            date_hint = f"{y}-{int(mo):02d}-{int(d):02d}"
            if args.date and args.date != date_hint:
                continue
            if not args.force and date_hint in existing:
                print(f"      [SKIP] {date_hint} 已在 CSV，跳过（--force 强制重写）")
                continue
            if date_hint in written_this_run:
                print(f"      [SKIP] {date_hint} 本次已写入（重复邮件）")
                continue

        with tempfile.TemporaryDirectory(prefix="bdi_mail_") as tmp:
            files = download_attachments(M, mid, Path(tmp))
            if not files:
                print(f"      [WARN] 邮件 {subj!r} 无 PDF/zip 附件")
                continue
            work = Path(tmp)
            for fp in files:
                if fp.suffix.lower() == ".zip":
                    with zipfile.ZipFile(fp) as z:
                        z.extractall(work)
            try:
                parsed = parse_day(str(work))
            except Exception as e:
                print(f"      [ERR] 解析失败: {e}")
                continue

            date_str = parsed.get("date") or date_hint or datetime.now().strftime("%Y-%m-%d")
            parsed["date"] = date_str

            json_path = JSON_DIR / f"bdi_{date_str}.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(parsed, f, indent=2, ensure_ascii=False)
            day_attach = ATTACH_DIR / date_str
            day_attach.mkdir(exist_ok=True)
            for p in work.rglob("*.pdf"):
                (day_attach / p.name).write_bytes(p.read_bytes())

            if date_str in existing or date_str in written_this_run:
                remove_date_row(date_str)
            append_row(parsed)
            written_this_run.add(date_str)
            success_dates.append(date_str)
            print(f"      [OK]  {date_str}  BDI={parsed.get('BDI')}  BCI={parsed.get('BCI')}  "
                  f"BPI={parsed.get('BPI')}  BSI={parsed.get('BSI')}  BHSI={parsed.get('BHSI')}")

    M.logout()

    print(f"\n[3/3] 完成。共写入 {len(success_dates)} 天:")
    for d in success_dates:
        print(f"        - {d}  →  {JSON_DIR / f'bdi_{d}.json'}")
    print(f"\n      CSV: {CSV_PATH}")


if __name__ == "__main__":
    main()
