"""
parse_bdi.py
解析 Baltic Exchange / BRS Brokers 每日 PDF（一个文件夹或 zip 包），输出标准化字典。

支持两类输入：
1) 来自 nora@reachyint.com 的 6 个 Baltic Exchange 标准官方 PDF
   (Baltic Dry/Capesize/Panamax/Supramax/Handysize Index + Dry TC Averages)
2) 来自 brsbrokers.com 的 BRS Daily Report 综合 PDF + 单独的 Baltic Capesize/
   Panamax(Asia)/Supramax(Asia) 等子 PDF

调用:
    from parse_bdi import parse_day
    result = parse_day("/path/to/folder_or_zip")
    print(result["date"], result["BDI"], result["BCI"])
"""
import re
import os
import subprocess
import sys
import zipfile
import tempfile
import fnmatch
from pathlib import Path
import pdfplumber
from datetime import datetime

PDF_READ_TIMEOUT_SECONDS = int(os.getenv("PDF_READ_TIMEOUT_SECONDS", "90"))


def _read_pdf_text(path: str) -> str:
    out = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            out.append(page.extract_text() or "")
    return "\n".join(out)


def _read_pdf_text_isolated(
    path: str, timeout_seconds: int = PDF_READ_TIMEOUT_SECONDS
) -> str:
    """Extract PDF text in a disposable process with a hard timeout."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--extract-text", path],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            f"PDF text extraction exceeded {timeout_seconds}s"
        ) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        raise RuntimeError(
            f"PDF text extraction exited {completed.returncode}{suffix}"
        )
    return completed.stdout


def _parse_date_official(text: str) -> str:
    """Match '22/Apr/2026' (Baltic Exchange dedicated PDFs)."""
    m = re.search(r"(\d{1,2})/([A-Za-z]{3})/(\d{4})", text)
    if not m:
        raise ValueError("Cannot find date")
    d, mon, y = m.groups()
    return datetime.strptime(f"{d} {mon} {y}", "%d %b %Y").strftime("%Y-%m-%d")


def _parse_date_brs(text: str) -> str:
    """Match '05 May 2026' (BRS Daily Report header)."""
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})", text)
    if not m:
        raise ValueError("Cannot find BRS date")
    d, mon, y = m.groups()
    fmt = "%d %B %Y" if len(mon) > 3 else "%d %b %Y"
    return datetime.strptime(f"{d} {mon} {y}", fmt).strftime("%Y-%m-%d")


def _to_num(s: str):
    s = s.replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        v = float(s)
        return int(v) if v.is_integer() else v
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Parsers — Baltic Exchange dedicated PDFs (nora@reachyint.com 旧链路)
# ---------------------------------------------------------------------------

def parse_bdi(text: str) -> dict:
    m = re.search(r"Baltic Dry Index\s+-?[\d,\.]+\s*[↑↓→]\s*\n\s*([\d,]+)", text)
    return {"BDI": _to_num(m.group(1))} if m else {}


def _parse_route_block(text: str, route_codes: list) -> dict:
    out = {}
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for code in route_codes:
        for ln in lines:
            if re.match(rf"^{re.escape(code)}\b", ln):
                tokens = ln.split()
                for tok in reversed(tokens):
                    v = _to_num(tok)
                    if v is not None:
                        out[code] = v
                        break
                break
    return out


def parse_bci(text: str) -> dict:
    out = {}
    m = re.search(r"Baltic Capesize Index\s+-?[\d,\.]+\s*[↑↓→]\s*\n\s*([\d,]+)", text)
    if m: out["BCI"] = _to_num(m.group(1))
    m = re.search(r"Capesize Timecharter Average \(C5TC[^\)]*\)\s+-?[\d,\.]+\s*[↑↓→]\s*\n\s*([\d,]+)", text)
    if m: out["C5TC"] = _to_num(m.group(1))

    routes = ["C8_182","C9_182","C10_182","C14_182","C16_182",
              "C2","C3","C5","C7","C17","C18_182"]
    out.update(_parse_route_block(text, routes))

    if "C5TC" in out:
        out["C5TC (182)"] = out["C5TC"]
    for c in ["C8","C9","C10","C14","C16"]:
        if f"{c}_182" in out:
            out[c] = out[f"{c}_182"]
    return out


def parse_bpi(text: str) -> dict:
    out = {}
    m = re.search(r"Baltic Panamax Index\s+-?[\d,\.]+\s*[↑↓→]\s*\n\s*([\d,]+)", text)
    if m: out["BPI"] = _to_num(m.group(1))
    m = re.search(r"Panamax Timecharter Average \(P5TC\)\s+-?[\d,\.]+\s*[↑↓→]\s*\n\s*([\d,]+)", text)
    if m: out["P5TC"] = _to_num(m.group(1))

    routes = ["P1A_82","P2A_82","P3A_82","P4_82","P5_82","P6_82",
              "P7","P8","P9_82","P10_82"]
    out.update(_parse_route_block(text, routes))
    if "P5TC" in out:
        out["P4TC"] = out["P5TC"]
    return out


def parse_bsi(text: str) -> dict:
    out = {}
    m = re.search(r"Baltic Supramax Index\s+-?[\d,\.]+\s*[↑↓→]\s*\n\s*([\d,]+)", text)
    if m: out["BSI"] = _to_num(m.group(1))
    m = re.search(r"Supramax Timecharter Average \(S11TC\)\s+-?[\d,\.]+\s*[↑↓→]\s*\n\s*([\d,]+)", text)
    if m: out["S11TC"] = _to_num(m.group(1))

    routes_63 = ["S1B_63","S1C_63","S2_63","S3_63","S4A_63","S4B_63",
                 "S5_63","S8_63","S9_63","S10_63","S11_63","S12_63","S15_63"]
    parsed = _parse_route_block(text, routes_63)
    for k, v in parsed.items():
        out[k.replace("_63", "")] = v
    if "S10" in out: out["S10TC"] = out.get("S11TC", out["S10"])
    return out


def parse_bhsi(text: str) -> dict:
    out = {}
    m = re.search(r"Baltic Handysize Index\s+-?[\d,\.]+\s*[↑↓→]\s*\n\s*([\d,]+)", text)
    if m: out["BHSI"] = _to_num(m.group(1))
    m = re.search(r"Handysize Timecharter Average \(HS7TC\)\s+-?[\d,\.]+\s*[↑↓→]\s*\n\s*([\d,]+)", text)
    if m: out["HS7TC"] = _to_num(m.group(1))

    routes = [f"HS{i}_38" for i in range(1, 8)]
    out.update(_parse_route_block(text, routes))
    return out


def parse_tc(text: str) -> dict:
    return {}


# ---------------------------------------------------------------------------
# Parsers — BRS Brokers PDFs
# ---------------------------------------------------------------------------

# Page 2 of BRS Daily Report has lines like:
#   "BDI 2,730 44 2,730 2,081"           (CODE  SPOT  CHANGE  MTD-AVG  YTD-AVG)
#   "BDI 2,677 ⇧ 11 2,417 2,058"         (some issues insert an arrow glyph
#                                          between SPOT and CHANGE)
#   "C5TC 36,828 1,087 36,828 25,100"
_BRS_HEADLINE_RE = re.compile(
    r"^(?P<code>BDI|BCI|BPI|BSI|BHSI|C5TC|P5TC|S10TC|HS7TC)\s+"
    r"(?P<spot>-?[\d,]+(?:\.\d+)?)"
    r"\s+(?:[↑↓→⇧⇩⇨]\s+)?"
    r"(?P<chg>-?[\d,]+(?:\.\d+)?)\b",
    re.MULTILINE,
)


def parse_brs_daily(text: str, back_calc: bool = False) -> dict:
    """Extract headline values from BRS Daily Report page 2 plaintext.

    Issue X reports the most recent Baltic close before publication time
    (typically X-1's close, or earlier if X-1 was a non-trading day).

    back_calc=False (default): return SPOT values (= "close of issue date - 1
        trading day"). Use for the row directly preceding the BRS Daily.
    back_calc=True: return SPOT - CHANGE for each code (= "close of issue
        date - 2 trading days"). Use when a single trading day falls between
        the target date and this BRS Daily issue (e.g. Apr 30 row paired
        with May 4 BRS Daily, with May 1 trading day in between).

    Provides BDI/BCI/BPI/BSI/BHSI + C5TC/P5TC/S10TC/HS7TC.
    """
    out = {}
    for m in _BRS_HEADLINE_RE.finditer(text):
        code = m.group("code")
        spot = _to_num(m.group("spot"))
        chg = _to_num(m.group("chg"))
        if spot is None:
            continue
        if back_calc:
            if chg is None:
                continue
            v = spot - chg
            if isinstance(spot, int) and isinstance(chg, int):
                v = int(v)
        else:
            v = spot
        out[code] = v
    if "P5TC" in out:
        out["P4TC"] = out["P5TC"]
    if "C5TC" in out:
        out.setdefault("C5TC (182)", out["C5TC"])
    if "S10TC" in out and "S11TC" not in out:
        out["S11TC"] = out["S10TC"]
    return out


def parse_bpi_asia(text: str) -> dict:
    """Baltic Panamax Index (Asia).pdf — only P5_82 route."""
    return _parse_route_block(text, ["P5_82"])


def parse_bsi_asia(text: str) -> dict:
    """Baltic Supramax Index (Asia).pdf — S2_63/S8_63/S10_63 + S3TC."""
    out = {}
    parsed = _parse_route_block(text, ["S2_63", "S8_63", "S10_63"])
    for k, v in parsed.items():
        out[k.replace("_63", "")] = v
    m = re.search(r"Baltic Supramax Asia Index \(S3TC\)\s+-?[\d,\.]+\s*[↑↓→]\s*\n\s*([\d,]+)", text)
    if m: out["S3TC_63"] = _to_num(m.group(1))
    return out


# ---------------------------------------------------------------------------
# File matchers — (glob pattern, parser kind, date-source priority)
# date_source: 'official' (高优先), 'brs', or None (不提供日期)
# Order matters: dedicated official PDFs override BRS Daily values.
# ---------------------------------------------------------------------------
FILE_MATCHERS = [
    ("Baltic Dry Index.pdf",            parse_bdi,        "official"),
    ("Baltic Capesize Index.pdf",       parse_bci,        "official"),
    ("Baltic Panamax Index.pdf",        parse_bpi,        "official"),
    ("Baltic Supramax Index.pdf",       parse_bsi,        "official"),
    ("Baltic Handysize Index.pdf",      parse_bhsi,       "official"),
    ("Dry TC Averages.pdf",             parse_tc,         None),
    ("Baltic Panamax Index (Asia).pdf", parse_bpi_asia,   "official"),
    ("Baltic Supramax Index (Asia).pdf",parse_bsi_asia,   "official"),
    ("BRS DAILY REPORT*.pdf",           parse_brs_daily,  "brs"),
]


def parse_day(folder_or_zip: str, extra_pdfs: list = None,
              skip_brs_daily_in_folder: bool = False,
              brs_back_calc: bool = False) -> dict:
    """Parse a folder (or zip) containing PDFs from the day. Returns merged dict.

    extra_pdfs: extra PDF paths to also parse (e.g., the NEXT day's BRS Daily,
        which carries this day's official headline close values).
    skip_brs_daily_in_folder: if True, ignore any "BRS DAILY REPORT" PDF inside
        the folder (because the in-folder BRS Daily reports the PREVIOUS day's
        close, which is wrong for this day's row).
    brs_back_calc: when True, BRS Daily headline values are taken as
        SPOT - CHANGE instead of SPOT — useful when one trading day falls
        between the target date and the BRS Daily issue. See
        parse_brs_daily() for details.
    """
    path = Path(folder_or_zip)
    if path.is_file() and path.suffix.lower() == ".zip":
        tmp = tempfile.mkdtemp(prefix="bdi_")
        with zipfile.ZipFile(path) as z:
            z.extractall(tmp)
        path = Path(tmp)

    pdfs = list(path.glob("*.pdf")) + list(path.rglob("*.pdf"))
    if skip_brs_daily_in_folder:
        pdfs = [p for p in pdfs if not fnmatch.fnmatch(p.name, "BRS DAILY REPORT*.pdf")]
    if extra_pdfs:
        pdfs.extend(Path(p) for p in extra_pdfs)
    seen = set(); pdfs_unique = []
    for p in pdfs:
        if p.resolve() in seen: continue
        seen.add(p.resolve()); pdfs_unique.append(p)

    # BRS Daily 应在最后解析（仅填补尚缺字段），先建索引
    plan = []  # (path, parser, date_source)
    for pdf in pdfs_unique:
        for pattern, parser, date_src in FILE_MATCHERS:
            if fnmatch.fnmatch(pdf.name, pattern):
                plan.append((pdf, parser, date_src))
                break

    # 排序：official 优先，brs 在后；这样 dedicated PDF 的值会先写，
    # 之后 BRS Daily 仅补缺，不覆盖
    plan.sort(key=lambda x: 0 if x[2] == "official" else 1)

    result = {}
    date_official = None
    date_brs = None
    for pdf, parser, date_src in plan:
        try:
            text = _read_pdf_text_isolated(str(pdf))
        except Exception as e:
            print(f"  [WARN] read failed {pdf.name}: {e}")
            continue
        if date_src == "official" and date_official is None:
            try: date_official = _parse_date_official(text)
            except Exception: pass
        if date_src == "brs" and date_brs is None:
            try: date_brs = _parse_date_brs(text)
            except Exception: pass
        try:
            if parser is parse_brs_daily:
                parsed = parser(text, back_calc=brs_back_calc)
            else:
                parsed = parser(text)
        except Exception as e:
            print(f"  [WARN] parse failed {pdf.name}: {e}")
            continue
        # BRS Daily 仅填补：不覆盖 dedicated PDFs 给出的值
        if date_src == "brs":
            for k, v in parsed.items():
                if v is None: continue
                if result.get(k) in (None, ""):
                    result[k] = v
        else:
            result.update({k: v for k, v in parsed.items() if v is not None})

    result["date"] = date_official or date_brs
    result["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return result


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--extract-text":
        sys.stdout.write(_read_pdf_text(sys.argv[2]))
    else:
        import json

        out = parse_day(sys.argv[1])
        print(json.dumps(out, indent=2, ensure_ascii=False))
