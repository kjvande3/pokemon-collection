#!/usr/bin/env python3
"""Backfill past TCGplayer prices from TCGCSV's daily archives.

TCGCSV keeps one archive per day since 2024-02-08:
  https://tcgcsv.com/archive/tcgplayer/prices-YYYY-MM-DD.ppmd.7z
Each holds <date>/<categoryId>/<groupId>/prices (same JSON as the live API).

We sample the 1st and 15th of every month (plus the archive's first day), only
for dates on or after each product's purchase date, and only download an
archive when some product still needs that date. Results go into
data/price_history.json, then the chart series is rebuilt.

Needs the `7z` command (preinstalled on GitHub's Ubuntu runners).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import history  # noqa: E402

ARCHIVE_URL = os.environ.get("ARCHIVE_URL", "https://tcgcsv.com/archive/tcgplayer/prices-{d}.ppmd.7z")
USER_AGENT = "pokemon-collection-dashboard/1.0 (+https://github.com/kjvande3/pokemon-collection)"
POKEMON = 3
MAX_DOWNLOADS = int(os.environ.get("BACKFILL_MAX_DOWNLOADS", "90"))


def sample_dates(start: date, end: date) -> list[date]:
    out = {history.ARCHIVE_START} if history.ARCHIVE_START >= start else set()
    d = date(start.year, start.month, 1)
    while d <= end:
        for day in (1, 15):
            s = d.replace(day=day)
            if start <= s <= end and s >= history.ARCHIVE_START:
                out.add(s)
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return sorted(out)


def download(d: date, dest: Path) -> bool:
    req = urllib.request.Request(ARCHIVE_URL.format(d=d.isoformat()), headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp, dest.open("wb") as fh:
            shutil.copyfileobj(resp, fh, 1 << 20)
        return True
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return False
        raise


def extract_prices(archive: Path, d: date, groups: set[int], workdir: Path) -> dict[int, dict]:
    """Return productId -> price record for the requested groups."""
    out_dir = workdir / "x"
    shutil.rmtree(out_dir, ignore_errors=True)
    wanted = [f"{d.isoformat()}/{POKEMON}/{g}/prices" for g in sorted(groups)]
    subprocess.run(["7z", "x", "-y", f"-o{out_dir}", str(archive), *wanted],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    found: dict[int, dict] = {}
    for g in groups:
        f = out_dir / d.isoformat() / str(POKEMON) / str(g) / "prices"
        if not f.exists():
            continue
        try:
            rows = json.loads(f.read_text()).get("results", [])
        except json.JSONDecodeError:
            continue
        for r in rows:
            pid = r.get("productId")
            if pid not in found or r.get("subTypeName") == "Normal":
                found[pid] = r
    return found


def main() -> int:
    if not shutil.which("7z"):
        print("7z not installed; skipping backfill")
        return 0
    portfolio = json.loads(history.PORTFOLIO.read_text())
    cache = json.loads(history.CACHE.read_text())
    products = history.tracked_products(portfolio, cache)
    if not products:
        print("No TCGplayer-matched products; nothing to backfill")
        return 0

    today = datetime.now(ZoneInfo("America/Chicago")).date()
    earliest = min(p["since"] for p in products.values())
    ph = history.load_price_history()

    # which (date -> productIds) still need a lookup
    todo: dict[date, set[int]] = {}
    for d in sample_dates(max(earliest, history.ARCHIVE_START), today - timedelta(days=1)):
        checked = set(ph["checked"].get(d.isoformat(), []))
        need = {pid for pid, p in products.items() if p["since"] <= d and pid not in checked}
        if need:
            todo[d] = need
    print(f"{len(products)} product(s), {len(todo)} archive date(s) to look up")

    done = 0
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for d, pids in sorted(todo.items()):
            if done >= MAX_DOWNLOADS:
                print(f"Reached {MAX_DOWNLOADS} downloads; the rest will be fetched next run")
                break
            archive = work / "a.7z"
            try:
                ok = download(d, archive)
            except Exception as err:  # noqa: BLE001 - transient; retry next run
                print(f"  {d}: download failed ({err}); will retry next run")
                continue
            done += 1
            key = d.isoformat()
            if ok:
                rows = extract_prices(archive, d, {products[p]["groupId"] for p in pids}, work)
                day = ph["prices"].setdefault(key, {})
                for pid in pids:
                    r = rows.get(pid)
                    price = r and (r.get("marketPrice") or r.get("midPrice"))
                    if price:
                        day[str(pid)] = round(float(price), 2)
                if not day:
                    ph["prices"].pop(key, None)
                print(f"  {d}: {len(day) if day else 0}/{len(pids)} priced")
            else:
                print(f"  {d}: no archive")
            ph["checked"][key] = sorted(set(ph["checked"].get(key, [])) | pids)
            archive.unlink(missing_ok=True)
            history.save_price_history(ph)  # save as we go

    series = history.rebuild(today)
    print(f"History rebuilt: {len(series)} points from {series[0]['date'] if series else '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
