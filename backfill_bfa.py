#!/usr/bin/env python3
"""Backfill missing TCE / FFADV columns into baltic_exchange_index_history.csv
from Nora's "XX MMM - BALTIC EXCHANGE DRY INDICES & FFA DATA" mails.

Each such mail attaches:
  - "BALTIC INDEX Baltic Exchange - Historic Data ...csv"  (Date, BCI, ..., C17-TCE, ..., S6TC, ...)
  - "FFA Baltic Exchange - Historic Data ...csv"            (Date, FFADV_*)
Both CSVs are date-keyed; we merge into the master history CSV in-place.

Usage:
    source .venv/bin/activate
    SSL_CERT_FILE=$(python3 -m certifi) python3 backfill_bfa.py
"""
import csv, email, imaplib, io, ssl
from datetime import datetime
from email.header import decode_header
from pathlib import Path

IMAP_HOST = "imap.qiye.aliyun.com"
IMAP_PORT = 993
IMAP_USER = "data@aquabridge.ai"
IMAP_PWD  = "bZ4NqiA4k8WK6iIb"

HERE     = Path(__file__).resolve().parent
CSV_PATH = HERE / "baltic_exchange_index_history.csv"

SUBJECT_KEY = "BALTIC EXCHANGE DRY"   # Nora's BFA digest mails
SENDER_KEY  = "reachyint"


def dec(s):
    if not s: return ""
    out = []
    for t, e in decode_header(s):
        out.append(t.decode(e or "utf-8", "replace") if isinstance(t, bytes) else t)
    return "".join(out)


def parse_date(s):
    """Accept '13-Apr-2026' or '2026-04-13' etc → 'YYYY-MM-DD'."""
    s = (s or "").strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%b-%y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def fetch_bfa_mails(M):
    typ, ids = M.search(None, "ALL")
    if typ != "OK" or not ids or not ids[0]:
        return []
    out = []
    for mid in ids[0].split():
        typ, data = M.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
        msg = email.message_from_bytes(data[0][1] if data and data[0] else b"")
        fr = (dec(msg.get("From", "")) or "").lower()
        if SENDER_KEY not in fr:
            continue
        s = dec(msg.get("Subject", "")) or ""
        if SUBJECT_KEY in s.upper() and "FFA DATA" in s.upper():
            out.append((mid, s))
    return out


def extract_csvs(M, mid):
    """Yield (filename, list[dict]) for each .csv attachment."""
    typ, data = M.fetch(mid, "(RFC822)")
    msg = email.message_from_bytes(data[0][1])
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        fn = dec(part.get_filename() or "")
        if not fn.lower().endswith(".csv"):
            continue
        payload = part.get_payload(decode=True) or b""
        text = payload.decode("utf-8", errors="replace")
        rdr = csv.DictReader(io.StringIO(text))
        rows = [dict(r) for r in rdr]
        yield fn, rows


def load_history():
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        rdr = csv.DictReader(f)
        cols = rdr.fieldnames
        rows = list(rdr)
    by_date = {r["date"]: r for r in rows if r.get("date")}
    return cols, rows, by_date


def write_history(cols, rows):
    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def merge_row(target, src, allowed_cols):
    """Set columns in `target` from `src` where target is empty/missing.
    Returns count of fields filled."""
    n = 0
    for k, v in src.items():
        if k not in allowed_cols:
            continue
        if v is None or str(v).strip() == "":
            continue
        cur = target.get(k, "")
        if cur is None or str(cur).strip() == "":
            target[k] = str(v)
            n += 1
    return n


def main():
    print(f"Loading history CSV: {CSV_PATH}")
    cols, rows, by_date = load_history()
    print(f"  {len(rows)} rows, {len(cols)} cols")
    col_set = set(cols)

    print(f"Logging into {IMAP_USER}@{IMAP_HOST}")
    M = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ssl.create_default_context())
    M.login(IMAP_USER, IMAP_PWD)
    M.select("INBOX", readonly=True)

    mails = fetch_bfa_mails(M)
    print(f"Found {len(mails)} BFA-digest mails")

    total_filled = 0
    new_dates = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for mid, subj in mails:
        print(f"\n[{mid.decode()}] {subj}")
        for fn, csv_rows in extract_csvs(M, mid):
            if "BALTIC INDEX" not in fn.upper() and "FFA Baltic Exchange" not in fn:
                # Only the 2 daily-data CSVs match history schema. Skip BFA forward curve CSVs.
                continue
            for src in csv_rows:
                d = parse_date(src.get("Date") or src.get("date"))
                if not d:
                    continue
                if d not in by_date:
                    blank = {c: "" for c in cols}
                    blank["date"] = d
                    rows.append(blank)
                    by_date[d] = blank
                    new_dates.append(d)
                filled = merge_row(by_date[d], src, col_set)
                if filled:
                    by_date[d]["updated_at"] = now
                    total_filled += filled
                    print(f"    {fn} -> {d}: filled {filled} fields")

    M.logout()

    rows.sort(key=lambda r: r.get("date") or "")
    write_history(cols, rows)
    print(f"\nDone. {total_filled} field-cells filled across {len(rows)} rows."
          f" New dates: {sorted(set(new_dates))}")
    print(f"  CSV: {CSV_PATH}")


if __name__ == "__main__":
    main()
