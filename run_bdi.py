#!/usr/bin/env python3
"""
run_bdi.py — Aquabridge BDI 一键抓取 + 解析 + 入库

每天运行一次:
    python3 run_bdi.py                                # 抓最近 7 天的 BRS 邮件
    python3 run_bdi.py --days 14                      # 回溯 14 天
    python3 run_bdi.py --date 2026-05-05              # 只处理指定那天
    python3 run_bdi.py --start 2026-05-01 --end 2026-05-10  # 处理日期区间

输出:
    1) 解析后的 JSON 文件 → ./output/json/bdi_YYYY-MM-DD.json
    2) 附件备份 → ./output/attachments/YYYY-MM-DD/
    3) Append 一行到 ./baltic_exchange_index_history.csv

依赖:
    pip3 install pdfplumber
"""
import argparse
import csv
import email
import imaplib
import json
import os
import re
import ssl
from datetime import datetime, timedelta
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 配置（修改这里即可）
# ---------------------------------------------------------------------------
# 凭据优先读环境变量（GitHub Actions 用 Secret 注入），回退到本地默认值
IMAP_HOST = os.getenv("IMAP_HOST", "imap.qiye.aliyun.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER = os.getenv("IMAP_USER", "data@aquabridge.ai")
IMAP_PWD  = os.getenv("IMAP_PWD",  "bZ4NqiA4k8WK6iIb")   # 三方客户端专用密码

# 监听的发件人（BRS Brokers 同时使用 zhu.hao-sheng.p@ 和 research.asia@ 等多个邮箱，
# 用域名做子串匹配可一次涵盖）
TARGET_SENDERS = [
    "brsbrokers.com",
]

# 不再用 SUBJECT_RE 过滤 —— BRS 一天会发 5~10 封不同主题的邮件，
# 需要把同一天所有 PDF 攒到一个文件夹，再交给 parse_day() 解析。

# 输出目录（脚本所在目录）
HERE = Path(__file__).resolve().parent
OUTPUT_DIR  = HERE / "output"
JSON_DIR    = OUTPUT_DIR / "json"
ATTACH_DIR  = OUTPUT_DIR / "attachments"
CSV_PATH    = HERE / "baltic_exchange_index_history.csv"

# CSV 列顺序（与你现有 csv 完全一致；不可解析的字段留空）
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
    parts = decode_header(s)
    out = []
    for text, enc in parts:
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
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    M = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ctx)
    M.login(IMAP_USER, IMAP_PWD)
    M.select("INBOX", readonly=True)
    return M


def fetch_brs_mails_by_day(M, start_date: datetime, end_date: datetime):
    """Group BRS mails by their email-Date UTC day.

    Aliyun IMAP 不支持 FROM 子串搜索：服务器只用 SINCE/BEFORE 过滤，
    发件人在客户端筛。返回 dict: 'YYYY-MM-DD' -> [(mid, fr, subj), ...]
    """
    since = start_date.strftime("%d-%b-%Y")
    before = (end_date + timedelta(days=1)).strftime("%d-%b-%Y")
    typ, ids = M.search(None, "SINCE", since, "BEFORE", before)
    if typ != "OK" or not ids or not ids[0]:
        return {}
    senders_lc = [s.lower() for s in TARGET_SENDERS]
    by_day = {}
    for mid in ids[0].split():
        typ, data = M.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT DATE FROM)])")
        if typ != "OK" or not data or not data[0]:
            continue
        raw = data[0][1] if isinstance(data[0], tuple) else b""
        msg = email.message_from_bytes(raw)
        fr = (decode_str(msg.get("From")) or "").lower()
        if not any(s in fr for s in senders_lc):
            continue
        subj = decode_str(msg.get("Subject")) or ""
        try:
            dt = parsedate_to_datetime(msg.get("Date") or "")
        except Exception:
            dt = None
        if dt is None:
            continue
        day = dt.astimezone().strftime("%Y-%m-%d")
        by_day.setdefault(day, []).append((mid, fr, subj))
    return by_day


def download_attachments(M, mid, dest_dir: Path):
    """Download PDF/zip attachments. Dedupes filename collisions across mails."""
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
        i = 1
        while target.exists():
            stem, ext = Path(fname).stem, Path(fname).suffix
            target = dest_dir / f"{stem}__{i}{ext}"
            i += 1
        payload = part.get_payload(decode=True)
        if not payload:
            continue
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


def _build_row(parsed: dict, existing_id: str = "") -> list:
    """Build one CSV row in CSV_COLUMNS order from a parsed dict."""
    row = []
    for col in CSV_COLUMNS:
        if col == "_id":
            row.append(existing_id)  # 保留后端已生成的 id（重写整行时不丢）
        elif col == "updated_at":
            row.append(parsed.get("updated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        else:
            v = parsed.get(col, "")
            row.append("" if v is None else v)
    return row


def upsert_row(parsed: dict) -> bool:
    """按 `date` upsert 一行：该日期已存在则就地替换整行，否则追加。

    返回 True 表示替换了已有行，False 表示新增。这样重跑（含 BRS Daily
    滞后字段补全）会更新原行，不再产生重复行。
    """
    ensure_csv()
    with open(CSV_PATH, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    header = rows[0]
    date_idx = header.index("date")
    id_idx = header.index("_id")
    target_date = parsed.get("date", "")
    replaced = False
    for i in range(1, len(rows)):
        r = rows[i]
        if len(r) > date_idx and r[date_idx] == target_date:
            existing_id = r[id_idx] if len(r) > id_idx else ""
            rows[i] = _build_row(parsed, existing_id)
            replaced = True
            break
    if not replaced:
        rows.append(_build_row(parsed))
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    return replaced


def existing_csv_dates() -> set:
    """Return the set of date strings already present in the CSV's `date` column."""
    if not CSV_PATH.exists():
        return set()
    out = set()
    with open(CSV_PATH, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = (row.get("date") or "").strip()
            if d:
                out.add(d)
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days",  type=int, default=7,  help="回溯天数 (default 7)")
    ap.add_argument("--date",  type=str, default=None, help="只处理指定日期 YYYY-MM-DD")
    ap.add_argument("--start", type=str, default=None, help="区间起始 YYYY-MM-DD")
    ap.add_argument("--end",   type=str, default=None, help="区间结束 YYYY-MM-DD")
    ap.add_argument("--force", action="store_true",   help="（已废弃：现在总是按 date upsert）")
    ap.add_argument("--no-fast-indices", action="store_true",
                    help="不使用东方财富/StockQ 快源覆盖 BDI/BCI/BPI/BSI/BHSI")
    args = ap.parse_args()

    # 计算时间区间
    if args.date:
        start = end = datetime.strptime(args.date, "%Y-%m-%d")
    elif args.start or args.end:
        if not (args.start and args.end):
            ap.error("--start 与 --end 必须同时给出")
        start = datetime.strptime(args.start, "%Y-%m-%d")
        end = datetime.strptime(args.end, "%Y-%m-%d")
    else:
        end = datetime.now()
        start = end - timedelta(days=args.days)
    if end < start:
        ap.error("--end 不能早于 --start")

    JSON_DIR.mkdir(parents=True, exist_ok=True)
    ATTACH_DIR.mkdir(parents=True, exist_ok=True)
    from parse_bdi import parse_day
    from fast_baltic_indices import FAST_CODES, fetch_fast_baltic_indices
    use_fast_indices = (
        not args.no_fast_indices
        and os.getenv("FAST_BALTIC_INDICES", "1").lower() not in {"0", "false", "no", "off"}
    )

    # 把搜索窗口往后扩 5 天，方便给最后一天的 BDI/BPI/... 找到「下一日 BRS Daily」
    fetch_end = end + timedelta(days=5)
    print(f"[1/3] 连接邮箱并按天分组 BRS 邮件（{start:%Y-%m-%d} → {fetch_end:%Y-%m-%d}） …")
    M = login()
    by_day = fetch_brs_mails_by_day(M, start, fetch_end)
    print(f"      命中 {sum(len(v) for v in by_day.values())} 封邮件，跨 {len(by_day)} 个日期")

    print(f"\n[2/3] 下载附件 …")
    for day in sorted(by_day):
        mails = by_day[day]
        day_attach = ATTACH_DIR / day
        day_attach.mkdir(exist_ok=True)
        print(f"  ── {day}（{len(mails)} 封）──")
        for mid, fr, subj in mails:
            saved = download_attachments(M, mid, day_attach)
            print(f"    [{mid.decode()}] {fr}  |  {subj}  ({len(saved)} attach)")
    M.logout()

    # 建索引：每一天文件夹里有没有 dedicated Capesize Index / BRS Daily Report
    def find_one(folder: Path, pattern: str):
        for p in sorted(folder.glob(pattern)):
            return p
        return None

    capesize_by_day = {}   # day -> Capesize Index PDF (= 该日为交易日)
    brs_daily_by_day = {}  # day -> BRS Daily Report PDF
    for day_folder in sorted(ATTACH_DIR.iterdir()):
        if not day_folder.is_dir(): continue
        day = day_folder.name
        cap = find_one(day_folder, "Baltic Capesize Index*.pdf")
        if cap: capesize_by_day[day] = cap
        brs = find_one(day_folder, "BRS DAILY REPORT*.pdf")
        if brs: brs_daily_by_day[day] = brs

    # 仅处理「请求区间 ∩ 交易日」(= 有 dedicated Capesize Index PDF 的日期)
    target_dates = []
    cur = start
    while cur <= end:
        d = cur.strftime("%Y-%m-%d")
        if d in capesize_by_day:
            target_dates.append(d)
        cur += timedelta(days=1)

    sorted_brs_days = sorted(brs_daily_by_day.keys())
    all_trading_days = sorted(capesize_by_day.keys())  # 所有已下载到 Capesize PDF 的日子

    print(f"\n[3/3] 解析 {len(target_dates)} 个交易日并写入 …")
    added_dates = []
    replaced_dates = []
    for date_str in target_dates:
        day_folder = ATTACH_DIR / date_str
        # 找下一个 BRS Daily（一般来说次个交易日的 Issue 才包含本日 close）
        next_brs_day = next((d for d in sorted_brs_days if d > date_str), None)
        next_brs = brs_daily_by_day[next_brs_day] if next_brs_day else None
        if next_brs is None:
            print(f"  [WARN] {date_str} 下一日 BRS Daily 暂未到达 —— BDI/BPI/BSI/BHSI 将留空")

        # 如果 date_str 与 next_brs_day 之间夹了一个交易日，
        # 则 BRS Daily 报告的是「夹在中间那个交易日」的收盘，需用 SPOT-CHANGE 回算
        back_calc = False
        if next_brs_day:
            between = [d for d in all_trading_days if date_str < d < next_brs_day]
            if len(between) == 1:
                back_calc = True
                print(f"  [INFO] {date_str} 与 {next_brs_day} BRS Daily 之间夹有交易日 {between[0]}，回算 SPOT-CHANGE")
            elif len(between) > 1:
                print(f"  [WARN] {date_str} 与 {next_brs_day} 之间有 {len(between)} 个交易日，BRS Daily 无法回算 —— BDI/BPI/BSI/BHSI 将留空")
                next_brs = None  # 放弃 BRS Daily 来源

        try:
            parsed = parse_day(
                str(day_folder),
                extra_pdfs=[str(next_brs)] if next_brs else None,
                skip_brs_daily_in_folder=True,
                brs_back_calc=back_calc,
            )
        except Exception as e:
            print(f"  [ERR] {date_str} 解析失败: {e}")
            continue

        # parsed["date"] 来自 Capesize Index PDF，应等于 date_str
        if parsed.get("date") and parsed["date"] != date_str:
            print(f"  [WARN] {date_str} 文件夹内 Capesize PDF 日期为 {parsed['date']}，按文件夹名修正")
        parsed["date"] = date_str

        if use_fast_indices:
            try:
                fast_indices = fetch_fast_baltic_indices(date_str)
            except Exception as e:
                print(f"  [WARN] {date_str} 快源指数采集失败，保留邮箱/PDF值: {e}")
            else:
                if fast_indices:
                    parsed.update(fast_indices)
                    got = ", ".join(f"{code}={parsed.get(code)}" for code in FAST_CODES if code in fast_indices)
                    missing = [code for code in FAST_CODES if code not in fast_indices]
                    print(f"  [FAST] {date_str} 使用快源覆盖: {got}")
                    if missing:
                        print(f"  [FAST] {date_str} 快源未返回: {', '.join(missing)}")
                else:
                    print(f"  [FAST] {date_str} 快源没有精确匹配日期，保留邮箱/PDF值")

        # 按 date upsert：重跑时更新原行（含 BRS Daily 滞后字段补全），不产生重复行
        if upsert_row(parsed):
            replaced_dates.append(date_str)
        else:
            added_dates.append(date_str)

        json_path = JSON_DIR / f"bdi_{date_str}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(parsed, f, indent=2, ensure_ascii=False)
        print(f"  [OK]  {date_str}  BDI={parsed.get('BDI')}  BCI={parsed.get('BCI')}  "
              f"BPI={parsed.get('BPI')}  BSI={parsed.get('BSI')}  BHSI={parsed.get('BHSI')}")

    print(f"\n[3/3] 完成。新增 {len(added_dates)} 行，更新 {len(replaced_dates)} 行。")
    for d in added_dates:
        print(f"        + {d}  →  {JSON_DIR / f'bdi_{d}.json'}")
    for d in replaced_dates:
        print(f"        ~ {d}  (CSV 行已更新)")
    print(f"\n      CSV: {CSV_PATH}")


if __name__ == "__main__":
    main()
