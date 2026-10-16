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


TCG_HISTORY_URL = "https://infinite-api.tcgplayer.com/price/history/{pid}/detailed?range={range}"
USER_AGENT = "pokemon-collection-dashboard/1.0 (+https://github.com/kjvande3/pokemon-collection)"


SEED_DIR = DATA / "tcgplayer_history"


def _read_seed(pid: int) -> list[tuple[str, float, int]]:
    import csv
    f = SEED_DIR / f"{pid}.csv"
    if not f.exists():
        return []
    with f.open(newline="") as fh:
        return sorted((r["date"], float(r["market_price"]), int(float(r.get("qty_sold") or 0)))
                      for r in csv.DictReader(fh) if float(r.get("market_price") or 0) > 0)


def _write_seed(pid: int, rows: list[tuple[str, float, int]]) -> None:
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["date,market_price,qty_sold"] + [f"{d},{p:g},{q}" for d, p, q in rows]
    (SEED_DIR / f"{pid}.csv").write_text("\n".join(lines) + "\n")


def tcgplayer_history(pid: int) -> list[tuple[str, float, int]]:
    """Weekly TCGplayer market price history, oldest first.

    Tries TCGplayer live; it refuses GitHub's servers, so when that fails we use
    the saved copy in data/tcgplayer_history/<productId>.csv (added by hand or
    from a run that could reach TCGplayer). A live result refreshes the saved copy.
    """
    try:
        live = _tcgplayer_history_live(pid)
    except Exception:  # noqa: BLE001
        live = []
    saved = _read_seed(pid)
    if live:
        merged = dict((d, (p, q)) for d, p, q in saved)
        merged.update((d, (p, q)) for d, p, q in live)
        rows = sorted((d, p, q) for d, (p, q) in merged.items())
        _write_seed(pid, rows)
        return rows
    if saved:
        return saved
    raise RuntimeError("TCGplayer unreachable and no saved history")


def _tcgplayer_history_live(pid: int) -> list[tuple[str, float, int]]:
    """Weekly TCGplayer market price history for a product: [(YYYY-MM-DD, price, qty_sold)], oldest first.

    This is the series behind the price chart on TCGplayer's product page; it goes
    back to roughly when TCGplayer started tracking the product.
    """
    import os
    import urllib.request

    out: dict[str, tuple[float, int]] = {}
    for rng in ("alltime", "annual"):  # annual fills the most recent weeks alltime can omit
        url = TCG_HISTORY_URL.format(pid=pid, range=rng)
        fixtures = os.environ.get("FIXTURE_DIR")
        if fixtures:
            f = Path(fixtures) / f"tcg-history-{pid}-{rng}.json"
            data = json.loads(f.read_text()) if f.exists() else {}
        else:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        for res in data.get("result") or []:
            if (res.get("condition") or "Unopened") not in ("Unopened", "Sealed", "Near Mint"):
                continue
            for b in res.get("buckets") or []:
                try:
                    price = float(b.get("marketPrice") or 0)
                    qty = int(float(b.get("quantitySold") or 0))
                except ValueError:
                    continue
                d = b.get("bucketStartDate")
                if price > 0 and d:
                    out[d] = (round(price, 2), qty)
    return sorted((d, p, q) for d, (p, q) in out.items())


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
    ph.setdefault("tcg_history_fetched", {})
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
            "mult": (m or {}).get("multiplier") or 1,
        })

    price_dates = sorted(d for d, v in ph["prices"].items() if v)
    purchase_dates = sorted({h["since"].isoformat() for h in holdings if h["since"]})
    dates = sorted(set(price_dates) | set(purchase_dates) | {today.isoformat()})

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
        if d == today.isoformat():
            value = portfolio["totals"]["value"]
        else:
            # items with no market price yet count at what was paid for them
            value = round(sum(
                h["qty"] * h["mult"] * last_price[h["pid"]] if h["pid"] and h["pid"] in last_price else h["cost"]
                for h in owned), 2)
        series.append({"date": d, "cost": cost, "value": value})

    _save(HISTORY, series)
    return series
