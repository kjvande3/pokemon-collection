"""Price history shared by the daily update and the archive backfill.

data/price_history.json
  {
    "prices":  {"YYYY-MM-DD": {"<tcgplayer productId>": price, ...}, ...},
    "checked": {"YYYY-MM-DD": [productId, ...]}   # archive dates already looked up
  }

data/history.json  (what the chart reads; rebuilt from the above every run)
  [{"date": "YYYY-MM-DD", "cost": invested-so-far, "value": market value or null}, ...]
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PRICE_HISTORY = DATA / "price_history.json"
HISTORY = DATA / "history.json"
PORTFOLIO = DATA / "portfolio.json"
CACHE = DATA / "match_cache.json"

ARCHIVE_START = date(2024, 2, 8)  # first day TCGCSV kept daily price archives


def _load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n")


def load_price_history() -> dict:
    ph = _load(PRICE_HISTORY, {})
    ph.setdefault("prices", {})
    ph.setdefault("checked", {})
    return ph


def save_price_history(ph: dict) -> None:
    _save(PRICE_HISTORY, ph)


def parse_date(text: str) -> date | None:
    try:
        return date.fromisoformat((text or "").strip())
    except ValueError:
        return None


def tracked_products(portfolio: dict, cache: dict) -> dict[int, dict]:
    """productId -> {groupId, since} for every collection item matched on TCGplayer."""
    out: dict[int, dict] = {}
    matches = cache.get("tcgplayer", {})
    for item in portfolio.get("items", []):
        m = matches.get(item["key"])
        if not m:
            continue
        pid = int(m["productId"])
        since = parse_date(item.get("purchase_date")) or ARCHIVE_START
        prev = out.get(pid)
        if not prev or since < prev["since"]:
            out[pid] = {"groupId": int(m["groupId"]), "since": since}
    return out


def rebuild(today: date) -> list[dict]:
    """Recompute the chart series from price history + the current collection."""
    portfolio = _load(PORTFOLIO, {})
    cache = _load(CACHE, {})
    ph = load_price_history()
    matches = cache.get("tcgplayer", {})
    items = portfolio.get("items", [])
    if not items:
        _save(HISTORY, [])
        return []

    holdings = []
    for it in items:
        m = matches.get(it["key"])
        holdings.append({
            "since": parse_date(it.get("purchase_date")),
            "qty": it["qty"],
            "cost": it["cost"],
            "pid": str(m["productId"]) if m else None,
        })

    price_dates = sorted(d for d, v in ph["prices"].items() if v)
    purchase_dates = sorted({h["since"].isoformat() for h in holdings if h["since"]})
    dates = sorted(set(price_dates) | set(purchase_dates) | {today.isoformat()})
    first_price = price_dates[0] if price_dates else today.isoformat()

    series = []
    last_price: dict[str, float] = {}
    pi = 0
    for d in dates:
        while pi < len(price_dates) and price_dates[pi] <= d:
            last_price.update(ph["prices"][price_dates[pi]])
            pi += 1
        dd = date.fromisoformat(d)
        owned = [h for h in holdings if h["since"] is None or h["since"] <= dd]
        if not owned:
            continue
        cost = round(sum(h["cost"] for h in owned), 2)
        value = None
        if d == today.isoformat():
            value = portfolio["totals"]["value"]
        elif d >= first_price:
            priced = [h for h in owned if h["pid"] and h["pid"] in last_price]
            if priced:
                value = round(sum(h["qty"] * last_price[h["pid"]] for h in priced), 2)
        series.append({"date": d, "cost": cost, "value": value})

    _save(HISTORY, series)
    return series
