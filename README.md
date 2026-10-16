# Sealed Collection Dashboard

A self-updating dashboard for a sealed Pokémon TCG collection: what you paid, what it's worth now, and the gain on each product and overall.

**Live site:** https://kjvande3.github.io/pokemon-collection/

## How it works

1. `collection.csv` is the list of everything you own.
2. A GitHub Action runs every day (and whenever the collection changes). It looks up current prices, writes them to `data/`, and redeploys the site.
3. The site in `site/` reads `data/portfolio.json` and `data/history.json`.

Nothing needs to run on your computer.

## Adding or changing products

Edit `collection.csv` on GitHub (pencil icon → commit). The site updates a minute or two later.

| Column | Required | Notes |
|---|---|---|
| `name` | yes | Product name. Abbreviations like ETB, UPC, PC are understood. |
| `set` | yes | Set name, e.g. `Evolving Skies`. Used to find the product on TCGplayer. |
| `type` | | ETB, Booster Box, Case… used for the "by product type" chart. |
| `qty` | yes | How many you currently own. |
| `unit_cost` | yes | What you paid per unit (no `$` needed). |
| `purchase_date` | | `YYYY-MM-DD` |
| `source` | | Where you bought it. |
| `tcgplayer_id` | | The number in a TCGplayer product URL (`tcgplayer.com/product/243722/...`). Optional, but pins the exact product. |
| `pricecharting_id` | | Optional; found automatically when PriceCharting is enabled. |
| `ebay_price` | | Your average of recent eBay **sold** listings, if you want eBay included. |
| `notes` | | Anything. |

Sold something? Lower `qty` or delete the row.

## Price sources

- **TCGplayer** — market price (recent sales) from [TCGCSV](https://tcgcsv.com), a free daily mirror of TCGplayer data. If a product has no recent sales, the middle listing price is used and marked "listing".
- **PriceCharting** — off until you add a subscription token: repo **Settings → Secrets and variables → Actions → New repository secret**, name `PRICECHARTING_TOKEN`.
- **eBay** — manual, from the `ebay_price` column (eBay's sold-items API isn't available to individuals).

The blended **market price** is a weighted average of whichever sources have a price; change the weights in `config.json`.

## Past prices (one-time backfill)

The value-over-time chart starts at your earliest purchase. Past market values come from TCGCSV's daily archives (available from Feb 8, 2024), but the archive host refuses GitHub's servers, so the backfill has to run once from your own computer:

1. Install [Python 3](https://www.python.org/downloads/) (tick "Add python.exe to PATH"), [7-Zip](https://www.7-zip.org/) and [Git](https://git-scm.com/downloads).
2. In a terminal:
   ```
   git clone https://github.com/kjvande3/pokemon-collection
   cd pokemon-collection
   python scripts/backfill_history.py
   git add data
   git commit -m "Backfill price history"
   git push
   ```

It samples the 1st and 15th of each month and only downloads what's missing, so re-run it after adding an item with an older purchase date. The daily GitHub run also retries a few dates each day in case access opens up; `data/backfill_log.txt` shows the last result.

## Run it now

**Actions → Refresh prices & deploy → Run workflow.**
