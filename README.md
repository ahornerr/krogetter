# Krogetter

Track sale prices and offers on Kroger family store websites (King Soopers, Kroger, Fred Meyer, Smith's, Ralphs, etc.). Get notified when items go on sale — including "Buy 2 Get 1 Free" style offers that the official Kroger API doesn't expose.

## How It Works

Krogetter uses [Camoufox](https://github.com/daijro/camoufox) (a stealth Firefox build) to load product pages on Kroger family store websites. The product page HTML contains a server-rendered `__INITIAL_STATE__` JSON blob with everything: prices, offers, promo descriptions, sale dates, and store-specific data. No API keys, no OAuth, no login required.

Store selection is done via the Kroger modality API — `POST /atlas/v1/modality/options` to find stores near a ZIP code, then `PUT /atlas/v1/modality/preferences` to select one. This sets a cookie that the server respects for subsequent page loads.

## Setup

```bash
# Clone
git clone git@github.com:ahornerr/krogetter.git
cd krogetter

# Create venv and install
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Download the Camoufox browser binary (one-time, ~700MB)
camoufox fetch
```

> **Note:** You need the [coryking fork of Camoufox](https://github.com/coryking/camoufox) (v142.0.1-fork.26+) which fixes Akamai detection. The default `pip install camoufox` installs this fork automatically as of v0.4.12.

## Usage

### Add a product to track

```bash
# Basic — auto-derives label from URL, uses IP geolocation for store
krogetter add "https://www.kingsoopers.com/p/coca-cola-vanilla-zero-sugar-fridge-pack-cans-12-fl-oz-12-pack/0004900004825"

# With a custom label
krogetter add "https://www.kingsoopers.com/p/.../0004900004825" --label "Coke Vanilla"

# Pickup at nearest store to a ZIP code
krogetter add "https://www.kingsoopers.com/p/.../0004900004825" --zip 80207

# Delivery pricing to a ZIP code
krogetter add "https://www.kingsoopers.com/p/.../0004900004825" --zip 80207 --delivery

# Pickup at a specific store (requires --zip to search)
krogetter add "https://www.kingsoopers.com/p/.../0004900004825" --zip 80207 --store 62000093
```

### Check for price changes

```bash
# One-shot check of all tracked items
krogetter check

# Check a specific item
krogetter check 0004900004825
```

### List tracked items

```bash
krogetter list
```

Output:
```
UPC              Label                        Store        Price      Eff/Unit   On Sale  Offer                Checked
------------------------------------------------------------------------------------------------------------------------------
0004900004825    Coca Cola Vanilla Zero Sug   PICK 80207   $11.99     $7.99       Yes      Buy 2 Get 1 Free     2026-07-10T05:39:54Z
```

- **Price**: Regular shelf price
- **Eff/Unit**: Effective per-unit price after applying the offer (e.g. "Buy 2 Get 1 Free" at $11.99 = $23.98 for 3 = $7.99/unit)
- **On Sale**: Yes if there's an active offer or price discount

### Run as a daemon

```bash
# Poll every hour (default)
krogetter run

# Poll every 30 minutes
krogetter run --interval 1800
```

### Remove a tracked item

```bash
krogetter remove 0004900004825
# or by URL
krogetter remove "https://www.kingsoopers.com/p/.../0004900004825"
```

### Show configuration

```bash
krogetter config
```

## Configuration

Configuration is loaded from environment variables and an optional TOML config file at `~/.config/krogetter/config.toml`:

| Env Var | Default | Description |
|---------|---------|-------------|
| `KROGETTER_LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `KROGETTER_DATA_DIR` | `~/.local/share/krogetter` | Where tracked items and history are stored |
| `KROGETTER_DEFAULT_CHAIN` | `KINGSOOPERS` | Default store chain |
| `KROGETTER_DEFAULT_ZIP` | _(none)_ | Default ZIP for store selection |
| `KROGETTER_POLL_INTERVAL` | `3600` | Polling interval in seconds |
| `KROGETTER_USE_WEB_FETCHER` | `true` | Use Camoufox web fetcher |

## Supported Stores

All 19 Kroger family brand domains are supported:

kingsoopers.com, kroger.com, fredmeyer.com, ralphs.com, smithsfoodanddrug.com, harristeeter.com, frysfood.com, qfc.com, dillons.com, bakersplus.com, citymarket.com, food4less.com, foodsco.net, gerbes.com, jaycfoods.com, marianos.com, metromarket.net, pay-less.com, picknsave.com

## Data Storage

- `tracked_items.json` — List of tracked items (URL, UPC, label, store settings)
- `history.jsonl` — Append-only price history (one JSON line per check per item)

Both files are in the data directory (default `~/.local/share/krogetter/`).

## How Offer Parsing Works

The `__INITIAL_STATE__` JSON contains an `offers[]` array with offer details:

```json
{
  "defaultDescription": "Buy 2 Get 1 Free",
  "displayTemplate": "MUST_BUY",
  "start": "2026-07-08T00:00:00",
  "end": "2026-07-21T23:59:59"
}
```

Krogetter parses "Buy N Get M Free" patterns to compute the effective unit price:
- "Buy 2 Get 1 Free" at $11.99 → pay $23.98 for 3 units → **$7.99/unit** (33.4% off)
- "Buy 1 Get 1 Free" at $5.00 → pay $5.00 for 2 units → **$2.50/unit** (50% off)

## Development

```bash
# Run tests
pytest tests/ -q

# Type checking
mypy src/krogetter/

# Linting
ruff check src/ tests/
```

## License

MIT
