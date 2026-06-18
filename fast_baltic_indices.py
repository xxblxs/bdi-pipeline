#!/usr/bin/env python3
"""Fast public collectors for Baltic headline indices.

This module intentionally returns only the five headline fields used to unblock
same-day sync: BDI, BCI, BPI, BSI and BHSI. Route, TCE and FFA fields should
still come from the email/PDF pipeline.
"""
from __future__ import annotations

import html
import json
import re
import ssl
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

EASTMONEY_INDUSTRY_INDEX_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
STOCKQ_BHSI_URL = "https://en.stockq.org/index/BHI.php"
NUM = r"[-+]?\d[\d,]*(?:\.\d+)?"

EASTMONEY_INDICES = {
    "BDI": "EMI00107664",
    "BCI": "EMI00107666",
    "BPI": "EMI00107665",
    "BSI": "EMI00107667",
}

FAST_CODES = ("BDI", "BCI", "BPI", "BSI", "BHSI")


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self.skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        value = clean_text(data)
        if value:
            self.parts.append(value)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def html_to_text(page: str) -> str:
    parser = TextExtractor()
    parser.feed(page)
    return " ".join(parser.parts)


def to_number(value: str | int | float | None) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value) if float(value).is_integer() else float(value)
    value = value.replace(",", "").strip()
    if not value or value in {"-", "--"}:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return int(parsed) if parsed.is_integer() else parsed


def http_get(url: str, timeout: int = 30) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    try:
        import certifi

        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context = ssl.create_default_context()
    with urlopen(request, timeout=timeout, context=context) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def normalize_date(value: str) -> str:
    value = clean_text(value)
    if not value:
        return ""
    for fmt, candidate in (
        ("%Y-%m-%d", value),
        ("%Y/%m/%d", value),
        ("%Y-%m-%d", value[:10]),
        ("%Y/%m/%d", value[:10]),
    ):
        try:
            return datetime.strptime(candidate, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return value[:10]


def parse_month_day(value: str) -> str:
    value = clean_text(value)
    year = datetime.now().year
    for fmt, candidate in (
        ("%Y-%m-%d", value),
        ("%Y/%m/%d", value),
        ("%Y %m/%d", f"{year} {value}"),
        ("%Y %b/%d", f"{year} {value}"),
        ("%Y %b %d", f"{year} {value}"),
    ):
        try:
            return datetime.strptime(candidate, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return value


def collect_eastmoney_indices(target_date: str | None = None) -> dict[str, Any]:
    values: dict[str, Any] = {}
    columns = "REPORT_DATE,INDICATOR_VALUE,CHANGE_RATE,IS_NEWEST,INDICATOR_ID,INDICATOR_NAME"
    for code, indicator_id in EASTMONEY_INDICES.items():
        params = {
            "reportName": "RPT_INDUSTRY_INDEX",
            "columns": columns,
            "filter": f'(INDICATOR_ID="{indicator_id}")',
            "pageNumber": "1",
            "pageSize": "20",
            "sortColumns": "REPORT_DATE",
            "sortTypes": "-1",
            "source": "WEB",
            "client": "WEB",
        }
        url = f"{EASTMONEY_INDUSTRY_INDEX_URL}?{urlencode(params)}"
        payload = json.loads(http_get(url))
        if not payload.get("success", False):
            raise ValueError(payload.get("message") or f"Eastmoney returned success=false for {code}")
        rows = payload.get("result", {}).get("data") or []
        for row in rows:
            row_date = normalize_date(str(row.get("REPORT_DATE") or ""))
            if target_date and row_date != target_date:
                continue
            value = to_number(row.get("INDICATOR_VALUE"))
            if value is not None:
                values[code] = value
            break
    return values


def collect_stockq_bhsi(target_date: str | None = None) -> dict[str, Any]:
    text = html_to_text(http_get(STOCKQ_BHSI_URL))
    latest = parse_stockq_latest_bhsi(text)
    if latest and (not target_date or latest[0] == target_date):
        return {"BHSI": latest[1]}

    for date_str, value in parse_stockq_history_bhsi(text):
        if not target_date or date_str == target_date:
            return {"BHSI": value}
    return {}


def parse_stockq_latest_bhsi(text: str) -> tuple[str, Any] | None:
    value_token = rf"(?:{NUM}|-)"
    header_match = re.search(r"Index\s+Change\s+Change%\s+High\s+Low\s+Open\s+YTD\s+local", text, re.I)
    if not header_match:
        return None
    tail = text[header_match.end() : header_match.end() + 240]
    latest_match = re.search(
        rf"({NUM})\s+({NUM})\s+({NUM})%\s+{value_token}\s+{value_token}\s+{value_token}\s+{NUM}%\s+(\d{{1,2}}/\d{{1,2}})",
        tail,
        re.I,
    )
    if not latest_match:
        return None
    value = to_number(latest_match.group(1))
    date_str = parse_month_day(latest_match.group(4))
    if value is None:
        return None
    return date_str, value


def parse_stockq_history_bhsi(text: str) -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    for match in re.finditer(rf"\b(\d{{4}}/\d{{2}}/\d{{2}})\s+({NUM})\b", text):
        date_str = normalize_date(match.group(1))
        value = to_number(match.group(2))
        if value is None:
            continue
        rows.append((date_str, value))
    return rows


def fetch_fast_baltic_indices(target_date: str | None = None) -> dict[str, Any]:
    """Return fast-source values for BDI/BCI/BPI/BSI/BHSI.

    When target_date is provided, a source value is used only if the source date
    matches exactly. This prevents a latest public quote from overwriting a
    historical backfill row.
    """
    values: dict[str, Any] = {}
    errors: list[str] = []
    for collector in (collect_eastmoney_indices, collect_stockq_bhsi):
        try:
            values.update(collector(target_date))
        except Exception as exc:
            errors.append(str(exc))
    if errors and not values:
        raise RuntimeError("; ".join(errors))
    return {code: values[code] for code in FAST_CODES if code in values}
