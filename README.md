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
| `cardmarket_id` | | Optional Cardmarket product ID to pin a match. `794902x6` prices the row as 6 of that product (e.g. a case of displays). |
| `notes` | | Anything. |

Sold something? Lower `qty` or delete the row.

## Price sources

- **TCGplayer** — market price (recent sales) from [TCGCSV](https://tcgcsv.com), a free daily mirror of TCGplayer data. If a product has no recent sales, the middle listing price is used and marked "listing".
- **Cardmarket** — Europe's largest TCG marketplace, from its free daily price guide. Uses the 30-day average sale price (falling back to shorter averages); converted from EUR at the ECB daily rate. A lowest-listing price is shown but not counted.

The blended **market price** is a weighted average of whichever sources have a price; change the weights in `config.json`.

## Price history

The value-over-time chart starts at your earliest purchase and uses TCGplayer's weekly market-price history (the series behind the chart on each TCGplayer product page). TCGplayer refuses requests from GitHub's servers, so that history is saved in `data/tcgplayer_history/<productId>.csv` and the daily job reads it from there. When a new product is added, its history file gets added too. Until a product has a price, the chart counts it at what you paid.

`scripts/backfill_history.py` can also be run from your own computer: it refreshes those files live and fills any gaps from TCGCSV's archives (needs 7-Zip). `data/backfill_log.txt` shows the last run.

## Run it now

**Actions → Refresh prices & deploy → Run workflow.**
