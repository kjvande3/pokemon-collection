#!/usr/bin/env python3
"""Refresh market prices for the sealed collection and write data for the dashboard.

Reads   collection.csv, config.json
Writes  data/portfolio.json   (everything the dashboard shows)
        data/history.json     (one snapshot per day, for the value-over-time chart)
        data/match_cache.json (resolved TCGplayer / Cardmarket product IDs)

Sources
  TCGplayer      - market price via TCGCSV (free daily mirror of TCGplayer prices)
  Cardmarket     - free daily EU price guide (EUR, converted at the ECB rate)

Standard library only, so the GitHub Action needs no pip install.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import history  # noqa: E402

LOCAL_TZ = ZoneInfo("America/Chicago")


def local_today() -> date:
    return datetime.now(LOCAL_TZ).date()

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
COLLECTION = ROOT / "collection.csv"
CONFIG = ROOT / "config.json"
PORTFOLIO = DATA / "portfolio.json"
HISTORY = DATA / "history.json"
CACHE = DATA / "match_cache.json"

TCGCSV = "https://tcgcsv.com/tcgplayer"
POKEMON_CATEGORY = 3
USER_AGENT = "pokemon-collection-dashboard/1.0 (+https://github.com/kjvande3/pokemon-collection)"
FIXTURES = os.environ.get("FIXTURE_DIR")  # offline testing: serve URLs from local files

DEFAULT_CONFIG = {
    "weights": {"tcgplayer": 1, "cardmarket": 1},
    "tcgplayer_price_field": "marketPrice",
    "match_min_confidence": 0.72,
    "spread_warning_pct": 25,
}


# ----------------------------------------------------------------------------- utils

def log(msg: str) -> None:
    print(msg, flush=True)


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=False) + "\n")


_http_cache: dict[str, object] = {}


def fetch_json(url: str, *, redact: str | None = None):
    """GET a JSON URL with retries. `redact` hides a secret in log output."""
    if url in _http_cache:
        return _http_cache[url]
    shown = url.replace(redact, "***") if redact else url
    if FIXTURES:
        parsed = urllib.parse.urlparse(url)
        name = (parsed.netloc + parsed.path).strip("/").replace("/", "__")
        if parsed.query:
            q = dict(urllib.parse.parse_qsl(parsed.query))
            q.pop("t", None)
            if q:
                name += "__" + "_".join(f"{k}-{v}" for k, v in sorted(q.items()))
        path = Path(FIXTURES) / f"{name}.json"
        if not path.exists():
            raise RuntimeError(f"no fixture for {shown} ({path.name})")
        data = json.loads(path.read_text())
        _http_cache[url] = data
        return data
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            _http_cache[url] = data
            time.sleep(0.25)  # be polite to free services
            return data
        except Exception as err:  # noqa: BLE001 - log and retry anything
            last_err = err
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET {shown} failed: {last_err}")


STOPWORDS = {"pokemon", "the", "tcg", "sv", "swsh", "sm", "xy", "me", "scarlet", "violet", "and"}


ABBREVIATIONS = {
    "etb": "elite trainer box",
    "upc": "ultra premium collection",
    "spc": "super premium collection",
    "pc": "pokemon center",
    "bb": "booster bundle",
    "bbox": "booster box",
}


def norm(text: str) -> str:
    text = (text or "").lower().replace("é", "e").replace("&", " and ")
    text = re.sub(r"\b(sv|swsh|sm|me)\d+[a-z]?\b", " ", text)  # set codes like SV08, ME06
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(ABBREVIATIONS.get(w, w) for w in text.split())


def tokens(text: str, drop_stop: bool = True) -> set[str]:
    toks = set(norm(text).split())
    return toks - STOPWORDS if drop_stop else toks


def similarity(a: str, b: str, ignore: set[str] | None = None) -> float:
    ta, tb = tokens(a), tokens(b)
    if ignore:
        # compare what's left once the set name is removed ("SS ETB" vs "Surging Sparks Elite Trainer Box")
        ra, rb = ta - ignore, tb - ignore
        if ra and rb:
            ta, tb = ra, rb
    if not ta or not tb:
        return 0.0
    jac = len(ta & tb) / len(ta | tb)
    seq = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return round(0.65 * jac + 0.35 * seq, 3)


def money(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return round(float(str(value).replace("$", "").replace(",", "").strip()), 2)
    except ValueError:
        return None


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def row_units(row: dict) -> int:
    """How many sealed units one collection item contains (e.g. 10 for an ETB case). Default 1."""
    try:
        return max(1, int(float(row.get("units") or 1)))
    except ValueError:
        return 1


def match_text(row: dict) -> str:
    """Name used to find the product: match_name if given (the single unit inside a case), else name."""
    return (row.get("match_name") or "").strip() or row["name"]


# ----------------------------------------------------------------------------- collection

def read_collection() -> list[dict]:
    rows: list[dict] = []
    seen: dict[str, int] = {}
    with COLLECTION.open(newline="", encoding="utf-8-sig") as fh:
        for raw in csv.DictReader(fh):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            if not row.get("name"):
                continue
            base = slug(row.get("id") or f"{row['name']}-{row.get('set','')}")
            seen[base] = seen.get(base, 0) + 1
            row["key"] = base if seen[base] == 1 else f"{base}-{seen[base]}"
            try:
                row["qty"] = max(0, int(float(row.get("qty") or 1)))
            except ValueError:
                row["qty"] = 1
            row["unit_cost"] = money(row.get("unit_cost")) or 0.0
            rows.append(row)
    return rows


# ----------------------------------------------------------------------------- TCGplayer via TCGCSV

class TCGplayer:
    def __init__(self, cache: dict, min_conf: float, price_field: str):
        self.cache = cache.setdefault("tcgplayer", {})
        self.min_conf = min_conf
        self.price_field = price_field
        self._groups = None

    def groups(self) -> list[dict]:
        if self._groups is None:
            self._groups = fetch_json(f"{TCGCSV}/{POKEMON_CATEGORY}/groups").get("results", [])
        return self._groups

    def products(self, group_id: int) -> list[dict]:
        return fetch_json(f"{TCGCSV}/{POKEMON_CATEGORY}/{group_id}/products").get("results", [])

    def prices(self, group_id: int) -> dict[int, dict]:
        out: dict[int, dict] = {}
        for p in fetch_json(f"{TCGCSV}/{POKEMON_CATEGORY}/{group_id}/prices").get("results", []):
            pid = p.get("productId")
            # sealed product is "Normal"; keep the first non-null entry otherwise
            if pid not in out or p.get("subTypeName") == "Normal":
                out[pid] = p
        return out

    @staticmethod
    def is_sealed(product: dict) -> bool:
        return not any(e.get("name") == "Number" for e in product.get("extendedData") or [])

    def candidate_groups(self, set_name: str) -> list[dict]:
        want = tokens(set_name)
        if not want:
            return []
        scored = []
        for g in self.groups():
            have = tokens(g.get("name", ""))
            if not have:
                continue
            cover = len(want & have) / len(want)
            if cover >= 0.5:
                # prefer full coverage, main sets, tighter names
                scored.append((cover, not g.get("isSupplemental", False), -len(have), g))
        scored.sort(key=lambda s: s[:3], reverse=True)
        return [s[3] for s in scored[:4]]

    def _record(self, product: dict, confidence: float, method: str, candidates=None) -> dict:
        rec = {
            "productId": product["productId"],
            "groupId": product["groupId"],
            "name": product.get("name"),
            "url": product.get("url"),
            "imageUrl": product.get("imageUrl"),
            "confidence": confidence,
            "method": method,
        }
        if candidates:
            rec["alternatives"] = candidates
        return rec

    def resolve(self, row: dict) -> dict | None:
        key = row["key"]
        # "194729" pins a product; "194729x10" prices this row as 10 of that product (e.g. a case of ETBs)
        m = re.match(r"\s*(\d+)\s*(?:[x×*]\s*(\d+))?\s*$", row.get("tcgplayer_id", "") or "")
        explicit_id = int(m.group(1)) if m else None
        mult = int(m.group(2) or 0) if m and m.group(2) else row_units(row)
        query_key = f"{match_text(row)}|{row.get('set','')}|x{mult}"
        cached = self.cache.get(key)
        if cached:
            if explicit_id is not None and cached.get("productId") == explicit_id and cached.get("multiplier", 1) == mult:
                return cached
            if explicit_id is None and cached.get("method") == "auto" and cached.get("query") == query_key:
                return cached

        groups = self.candidate_groups(row.get("set", ""))

        if explicit_id is not None:
            search = groups + [g for g in self.groups() if g not in groups]
            for g in search:
                for p in self.products(g["groupId"]):
                    if p.get("productId") == explicit_id:
                        rec = self._record(p, 1.0, "id")
                        rec["multiplier"] = mult
                        self.cache[key] = rec
                        return rec
            log(f"  ! TCGplayer id {explicit_id} not found for {row['name']}")
            return None

        if not groups:
            log(f"  ! no TCGplayer set matched '{row.get('set')}' for {row['name']}")
            return None

        base = match_text(row)
        query = base if row.get("match_name") or not row.get("type") or norm(row["type"]) in norm(base) else f"{base} {row['type']}"
        scored = []
        for g in groups:
            set_words = tokens(g.get("name", "")) | tokens(row.get("set", ""))
            initials = "".join(w[0] for w in norm(re.sub(r"^[^:]*:", "", g.get("name", ""))).split() if w not in STOPWORDS)
            ignore = set_words | {initials, (g.get("abbreviation") or "").lower()}
            for p in self.products(g["groupId"]):
                if not self.is_sealed(p):
                    continue
                scored.append((similarity(query, p.get("cleanName") or p.get("name", ""), ignore), p))
        if not scored:
            return None
        scored.sort(key=lambda s: s[0], reverse=True)
        best_score, best = scored[0]
        alts = [{"productId": p["productId"], "name": p.get("name"), "score": s} for s, p in scored[1:4]]
        conf = best_score
        if len(scored) > 1 and best_score - scored[1][0] < 0.03:
            conf = round(best_score - 0.1, 3)  # near-tie: treat as uncertain
        rec = self._record(best, conf, "auto", alts)
        rec["query"] = query_key
        rec["multiplier"] = mult
        self.cache[key] = rec
        return rec

    def quote(self, rec: dict) -> dict | None:
        p = self.prices(rec["groupId"]).get(rec["productId"])
        if not p:
            return None
        # marketPrice comes from recent sales; rarely-sold items (e.g. cases) only have listing prices
        basis = "market"
        price = p.get(self.price_field) or p.get("marketPrice")
        if price is None:
            # TCGCSV omits market price for rarely-sold items; TCGplayer's own chart still has one
            try:
                series = history.tcgplayer_history(rec["productId"])
                if series:
                    price = series[-1][1]
            except Exception as err:  # noqa: BLE001
                log(f"  ! TCGplayer price history unavailable: {err}")
        if price is None and p.get("midPrice") is not None:
            price, basis = p["midPrice"], "listing"
        if price is None:
            return None
        mult = rec.get("multiplier") or 1
        return {
            "price": round(float(price) * mult, 2),
            "unit_price": round(float(price), 2),
            "multiplier": mult,
            "basis": basis,
            "low": p.get("lowPrice"),
            "mid": p.get("midPrice"),
            "url": rec.get("url"),
            "matched_name": f"{mult} × {rec.get('name')}" if mult > 1 else rec.get("name"),
            "confidence": rec.get("confidence"),
        }


class Cardmarket:
    """Cardmarket (EU) public daily price guide for Pokémon sealed product. Free, no key.

    products_nonsingles_6.json -> idProduct + name for sealed product
    price_guide_6.json         -> avg / low / trend / avg1 / avg7 / avg30 (EUR) per idProduct
    """

    BASE = "https://downloads.s3.cardmarket.com/productCatalog"
    # words that change what's in the box; a match must agree on all of them
    KIND_WORDS = {"case", "display", "bundle", "box", "pack", "packs", "blister", "tin", "collection", "set", "half", "build", "battle", "premium", "ultra", "super", "center", "elite", "trainer", "booster", "sleeved", "mini"}

    def __init__(self, cache: dict, min_conf: float):
        self.cache = cache.setdefault("cardmarket", {})
        self.min_conf = min_conf
        catalog = fetch_json(f"{self.BASE}/productList/products_nonsingles_6.json")
        self.products = catalog.get("products") or []
        guide = fetch_json(f"{self.BASE}/priceGuide/price_guide_6.json")
        self.guide = {g["idProduct"]: g for g in guide.get("priceGuides") or []}
        self.updated = guide.get("createdAt")
        self.eur_usd = eur_to_usd()
        self._index = [(p, tokens(p.get("name", ""))) for p in self.products]

    def resolve(self, row: dict) -> dict | None:
        # "794902" pins a product; "794902x6" prices this row as 6 of that product (e.g. a case of displays)
        m = re.match(r"\s*(\d+)\s*(?:[x×*]\s*(\d+))?\s*$", row.get("cardmarket_id", "") or "")
        if m:
            pid, mult = int(m.group(1)), int(m.group(2) or row_units(row))
            p = next((p for p in self.products if p.get("idProduct") == pid), None)
            return {"idProduct": pid, "name": p.get("name") if p else None, "confidence": 1.0, "method": "id", "multiplier": mult}
        mult = row_units(row)
        query_key = f"{match_text(row)}|{row.get('set','')}|x{mult}"
        cached = self.cache.get(row["key"])
        if cached and cached.get("query") == query_key:
            return cached if cached.get("idProduct") else None

        set_words = tokens(row.get("set", ""))
        name = re.sub(r"\([^)]*\)", " ", match_text(row))  # drop notes like "(10 ETBs)"
        query = name if set_words <= tokens(name) else f"{name} {row.get('set','')}"
        qt = tokens(query)
        q_kind = qt & self.KIND_WORDS
        best, best_score = None, 0.0
        near = []
        for p, pt in self._index:
            if set_words and not set_words <= pt:
                continue  # must be the same set
            s = similarity(query, p.get("name", ""), set_words)
            near.append((s, p))
            if (pt & self.KIND_WORDS) != q_kind:
                continue  # an ETB is not an ETB case
            if s > best_score:
                best, best_score = p, s
        near.sort(key=lambda x: x[0], reverse=True)
        rec = {"query": query_key, "idProduct": None, "multiplier": mult,
               "candidates": [{"idProduct": p["idProduct"], "name": p.get("name"), "score": sc} for sc, p in near[:5]]}
        if best and best_score >= self.min_conf:
            rec.update({"idProduct": best["idProduct"], "name": best.get("name"), "confidence": best_score, "method": "auto"})
        self.cache[row["key"]] = rec
        return rec if rec["idProduct"] else None

    def quote(self, rec: dict) -> dict | None:
        g = self.guide.get(rec["idProduct"])
        if not g:
            return None
        # sale-based fields first; "low" is only the cheapest current listing
        for field in ("avg30", "avg7", "avg1", "avg", "trend", "low"):
            eur = g.get(field)
            if eur:
                break
        else:
            return None
        mult = rec.get("multiplier") or 1
        name = rec.get("name") or ""
        eur = float(eur) * mult
        return {
            "price": round(eur * self.eur_usd, 2),
            "eur": round(eur, 2),
            "basis": field,
            "listing_only": field == "low",
            "multiplier": mult,
            "matched_name": f"{mult} × {name}" if mult > 1 else name,
            "url": "https://www.cardmarket.com/en/Pokemon/Products/Search?searchString=" + urllib.parse.quote(name),
            "confidence": rec.get("confidence"),
        }


def eur_to_usd() -> float:
    """Latest ECB reference rate (via Frankfurter, falling back to the ECB's own feed)."""
    try:
        return float(fetch_json("https://api.frankfurter.app/latest?from=EUR&to=USD")["rates"]["USD"])
    except Exception:  # noqa: BLE001
        pass
    req = urllib.request.Request("https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        xml = resp.read().decode("utf-8")
    m = re.search(r"currency='USD'\s+rate='([\d.]+)'", xml)
    if not m:
        raise RuntimeError("no EUR/USD rate")
    return float(m.group(1))


# ----------------------------------------------------------------------------- main

def blend(prices: dict, weights: dict) -> float | None:
    num = den = 0.0
    for src, q in prices.items():
        w = float(weights.get(src, 0) or 0)
        if q and q.get("price") is not None and w > 0:
            num += w * q["price"]
            den += w
    return round(num / den, 2) if den else None


def main() -> int:
    cfg = {**DEFAULT_CONFIG, **load_json(CONFIG, {})}
    weights = {**DEFAULT_CONFIG["weights"], **cfg.get("weights", {})}
    rows = read_collection()
    log(f"Collection: {len(rows)} rows")

    cache = load_json(CACHE, {})
    previous = {i["key"]: i for i in load_json(PORTFOLIO, {}).get("items", [])}
    status: dict[str, str] = {}

    tcg = TCGplayer(cache, cfg["match_min_confidence"], cfg["tcgplayer_price_field"])
    try:
        cm = Cardmarket(cache, cfg["match_min_confidence"])
        status["cardmarket"] = "ok"
        log(f"Cardmarket: {len(cm.products)} sealed products, guide {cm.updated}, EUR->USD {cm.eur_usd}")
    except Exception as err:  # noqa: BLE001
        cm = None
        status["cardmarket"] = f"unavailable ({err}) - showing last known prices"
        log(f"! Cardmarket unavailable: {err}")

    tcg_errors = 0
    items = []
    for row in rows:
        log(f"- {row['name']}")
        prices: dict[str, dict | None] = {"tcgplayer": None, "cardmarket": None}
        flags: list[str] = []
        image = None
        prev = previous.get(row["key"], {})

        # TCGplayer
        try:
            rec = tcg.resolve(row)
            if rec:
                image = rec.get("imageUrl")
                q = tcg.quote(rec)
                if q:
                    prices["tcgplayer"] = q
                    if q.get("basis") == "listing":
                        flags.append("listing_price")
                    if rec.get("method") == "auto" and (rec.get("confidence") or 0) < cfg["match_min_confidence"]:
                        flags.append("check_match")
                        prices["tcgplayer"]["uncertain"] = True
                        prices["tcgplayer"]["alternatives"] = rec.get("alternatives", [])
            else:
                flags.append("no_tcgplayer_match")
        except Exception as err:  # noqa: BLE001
            tcg_errors += 1
            log(f"  ! TCGplayer: {err}")
            old = (prev.get("prices") or {}).get("tcgplayer")
            if old:
                prices["tcgplayer"] = {**old, "stale": True}
                image = prev.get("image")

        # Cardmarket
        if cm:
            try:
                rec = cm.resolve(row)
                if rec:
                    prices["cardmarket"] = cm.quote(rec)
                    if prices["cardmarket"]:
                        log(f"  Cardmarket: {rec.get('name')} -> ${prices['cardmarket']['price']}")
                else:
                    log("  Cardmarket: no confident match (add cardmarket_id to pin one)")
            except Exception as err:  # noqa: BLE001
                log(f"  ! Cardmarket: {err}")
        else:
            old = (prev.get("prices") or {}).get("cardmarket")
            if old:
                prices["cardmarket"] = {**old, "stale": True}

        # Low-confidence matches and asking-price-only quotes are shown but not counted,
        # unless nothing better exists for the item
        def weak(q):
            return bool(q and (q.get("uncertain") or q.get("listing_only")))
        counted = dict(prices)
        if any(q and not weak(q) for q in prices.values()):
            counted = {k: (None if weak(q) else q) for k, q in prices.items()}
        market = blend(counted, weights)
        for k, q in prices.items():
            if q:
                q["counted"] = counted.get(k) is not None

        quoted = [q["price"] for q in counted.values() if q and q.get("price")]
        spread = None
        if len(quoted) >= 2:
            lo, hi = min(quoted), max(quoted)
            spread = round((hi - lo) / ((hi + lo) / 2) * 100, 1)
            if spread >= cfg["spread_warning_pct"]:
                flags.append("sources_disagree")
        if any(q and q.get("stale") for q in prices.values()):
            flags.append("stale")

        qty, unit = row["qty"], row["unit_cost"]
        cost = round(qty * unit, 2)
        if market is None:
            flags.append("unpriced")
        value = round(qty * market, 2) if market is not None else None
        gain = round(value - cost, 2) if value is not None else None
        items.append({
            "key": row["key"],
            "name": row["name"],
            "set": row.get("set", ""),
            "type": row.get("type", ""),
            "qty": qty,
            "unit_cost": unit,
            "cost": cost,
            "purchase_date": row.get("purchase_date", ""),
            "bought_from": row.get("source", ""),
            "notes": row.get("notes", ""),
            "image": image,
            "prices": prices,
            "market": market,
            "value": value,
            "gain": gain,
            "gain_pct": round(gain / cost * 100, 1) if gain is not None and cost else None,
            "spread_pct": spread,
            "flags": flags,
        })

    status["tcgplayer"] = "ok" if tcg_errors == 0 else f"{tcg_errors} lookup(s) failed - showing last known prices"

    priced = [i for i in items if i["value"] is not None]
    cost_all = round(sum(i["cost"] for i in items), 2)
    cost_priced = round(sum(i["cost"] for i in priced), 2)
    value_priced = round(sum(i["value"] for i in priced), 2)
    gain = round(value_priced - cost_priced, 2)
    now = datetime.now(timezone.utc)
    totals = {
        "cost": cost_all,
        "cost_priced": cost_priced,
        "value": value_priced,
        "gain": gain,
        "gain_pct": round(gain / cost_priced * 100, 1) if cost_priced else None,
        "units": sum(i["qty"] for i in items),
        "products": len(items),
        "priced_products": len(priced),
    }
    save_json(PORTFOLIO, {
        "generated_at": now.isoformat(timespec="seconds"),
        "status": status,
        "weights": weights,
        "totals": totals,
        "items": items,
    })

    # drop matches for rows that no longer exist (renamed, sold, edited)
    keys = {i["key"] for i in items}
    for src in ("tcgplayer", "cardmarket"):
        if src in cache:
            cache[src] = {k: v for k, v in cache[src].items() if k in keys}
    save_json(CACHE, cache)

    # record today's TCGplayer prices, then rebuild the chart series
    today = local_today()
    ph = history.load_price_history()
    day = ph["prices"].setdefault(today.isoformat(), {})
    for i in items:
        m = cache.get("tcgplayer", {}).get(i["key"])
        q = i["prices"].get("tcgplayer")
        if m and q and q.get("price") is not None and not q.get("stale"):
            day[str(m["productId"])] = q.get("unit_price", q["price"])
    history.save_price_history(ph)
    history.rebuild(today)

    log(f"Done: value ${value_priced:,.2f} vs cost ${cost_priced:,.2f} ({len(priced)}/{len(items)} priced)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
