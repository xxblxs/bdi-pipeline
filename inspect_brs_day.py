#!/usr/bin/env python3
"""
inspect_brs_day.py — 一次性诊断工具：列出指定日期内 brsbrokers 转发的所有邮件，
下载 PDF 附件，跑 parse_bdi.parse_day() 看字段覆盖率。

用法:
    SSL_CERT_FILE=$(python3 -m certifi) python3 inspect_brs_day.py --date 2026-05-04

Phase A 用途：确认换源到 Zhu.Hao-Sheng.p@brsbrokers.com 后哪些邮件凑齐了所需字段。
"""
import argparse
import email
import imaplib
import json
import re
import ssl
from datetime import datetime, timedelta
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path

# 复用 run_bdi.py 中的 IMAP 配置
from run_bdi import IMAP_HOST, IMAP_PORT, IMAP_USER, IMAP_PWD, decode_str

BRS_SENDERS = ["zhu.hao-sheng.p@brsbrokers.com", "brsbrokers.com"]

HERE = Path(__file__).resolve().parent
ATTACH_ROOT = HERE / "output" / "attachments"
REFERENCE_JSON = HERE / "output" / "json" / "bdi_2026-04-22.json"


def login():
    print(f"  ↪ Login {IMAP_USER}@{IMAP_HOST}:{IMAP_PORT}")
    ctx = ssl.create_default_context()
    M = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ctx)
    M.login(IMAP_USER, IMAP_PWD)
    M.select("INBOX", readonly=True)
    return M


def search_day(M, target_date: datetime):
    """SINCE date BEFORE date+1 (UTC day), then client-side filter by FROM.

    We deliberately use a tight one-day window so attachments don't bleed
    across reports for adjacent business days.
    """
    since = target_date.strftime("%d-%b-%Y")
    before = (target_date + timedelta(days=1)).strftime("%d-%b-%Y")
    typ, ids = M.search(None, "SINCE", since, "BEFORE", before)
    if typ != "OK" or not ids or not ids[0]:
        return []
    senders_lc = [s.lower() for s in BRS_SENDERS]
    out = []
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
        date_hdr = msg.get("Date") or ""
        try:
            dt = parsedate_to_datetime(date_hdr)
        except Exception:
            dt = None
        out.append((mid, fr, dt, subj))
    return out


def download_attachments(M, mid, dest_dir: Path):
    """Download PDF/zip attachments. Returns list of saved paths."""
    typ, data = M.fetch(mid, "(RFC822)")
    if typ != "OK":
        return []
    msg = email.message_from_bytes(data[0][1])
    saved = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        fname = part.get_filename()
        if not fname:
            continue
        fname = decode_str(fname)
        if not (fname.lower().endswith(".pdf") or fname.lower().endswith(".zip")):
            continue
        target = dest_dir / fname
        # de-dup if same filename comes from multiple mails
        i = 1
        while target.exists():
            stem = Path(fname).stem
            ext = Path(fname).suffix
            target = dest_dir / f"{stem}__{i}{ext}"
            i += 1
        with open(target, "wb") as f:
            payload = part.get_payload(decode=True)
            if payload:
                f.write(payload)
        saved.append(target)
    return saved


def field_coverage_report(parsed: dict):
    """Compare parsed dict to REFERENCE_JSON; print covered / missing fields."""
    if not REFERENCE_JSON.exists():
        print(f"  (reference file not found: {REFERENCE_JSON})")
        return
    with open(REFERENCE_JSON) as f:
        ref = json.load(f)
    ref_keys = [k for k in ref.keys() if k not in {"updated_at"}]

    covered, missing = [], []
    for k in ref_keys:
        v = parsed.get(k)
        if v in (None, "", 0) and k != "BHSI":  # 0 might be legitimate; show it
            # treat None/missing as missing; keep zeros as covered
            if k not in parsed or parsed[k] is None:
                missing.append(k)
                continue
        covered.append(k)

    headline = ["BDI", "BCI", "BPI", "BSI", "BHSI", "C5TC", "P5TC", "S11TC", "HS7TC", "date"]
    print("\n=== Headline values ===")
    for k in headline:
        v = parsed.get(k)
        flag = "✓" if v not in (None, "") else "✗"
        print(f"  {flag} {k:<8} = {v}")

    print(f"\n=== Field coverage vs {REFERENCE_JSON.name} ===")
    print(f"  Covered: {len(covered)} / {len(ref_keys)}")
    if missing:
        print(f"  Missing ({len(missing)}): {', '.join(missing)}")
    else:
        print("  Missing: (none)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="Target date YYYY-MM-DD (mail Date or report date)")
    args = ap.parse_args()

    target_date = datetime.strptime(args.date, "%Y-%m-%d")
    day_dir = ATTACH_ROOT / args.date
    day_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/3] Search brsbrokers mails on/around {args.date} …")
    M = login()
    try:
        mails = search_day(M, target_date)
        print(f"      Found {len(mails)} matching mails")

        for i, (mid, fr, dt, subj) in enumerate(mails, 1):
            print(f"\n  [Mail {i}] mid={mid.decode()}")
            print(f"    From   : {fr}")
            print(f"    Date   : {dt}")
            print(f"    Subject: {subj}")
            saved = download_attachments(M, mid, day_dir)
            for p in saved:
                size = p.stat().st_size
                print(f"    Attach : {p.name}  ({size} bytes)")
            if not saved:
                print("    Attach : (none)")
    finally:
        M.logout()

    print(f"\n[2/3] All attachments saved under: {day_dir}")
    pdfs = sorted(day_dir.glob("*.pdf"))
    print(f"      {len(pdfs)} PDF files:")
    for p in pdfs:
        print(f"        - {p.name}")

    print(f"\n[3/3] Run parse_bdi.parse_day() on attachment dir …")
    from parse_bdi import parse_day
    try:
        parsed = parse_day(str(day_dir))
    except Exception as e:
        print(f"      [ERR] parse_day failed: {e}")
        return
    field_coverage_report(parsed)

    # Persist a debug dump for the user to inspect
    debug_path = day_dir / "_parse_result.json"
    with open(debug_path, "w") as f:
        json.dump(parsed, f, indent=2, ensure_ascii=False)
    print(f"\n      Debug parse result: {debug_path}")


if __name__ == "__main__":
    main()
