# iv-snapshotter

Daily ATM implied-volatility snapshots for a configurable list of underlyings.
Runs as a free GitHub Actions cron, commits one CSV row per
`(snapshot_date, expiration)` per ticker into `data/`. Consumed by the
MarketDesk Options AI tab to render historical IV-ratio charts for calendar
spreads.

No credentials required — yfinance hits Yahoo's public option-chain API.

## Setup

1. **Create a new GitHub repo** (public is fine; the data isn't sensitive).
   Suggested name: `iv-snapshots`.

2. **Push this directory to that repo:**

   ```bash
   cd iv-snapshotter
   git init -b main
   git add .
   git commit -m "initial commit"
   git remote add origin git@github.com:YOUR_USERNAME/iv-snapshots.git
   git push -u origin main
   ```

3. **Enable Actions** in the repo Settings → Actions → General → "Allow all
   actions". Also confirm Workflow permissions = "Read and write" so the
   bot can commit CSV updates.

4. **Trigger a first run manually** to validate: Actions tab → "Daily IV
   Snapshot" → "Run workflow". A successful run will commit
   `data/{TICKER}.csv` files. After that the schedule (22:00 UTC weekdays)
   takes over.

5. **Point the MarketDesk app at this repo** by setting the
   `iv_history_base_url` credential to the raw URL of the `data/`
   directory. From the backend dir:

   ```bash
   cd market-dashboard/backend
   python3 -c "from services.credentials import set_credential; \
     set_credential('iv_history_base_url', \
       'https://raw.githubusercontent.com/YOUR_USERNAME/iv-snapshots/main/data')"
   ```

## Tickers

Edit `tickers.txt`. Format: one symbol per line. Optional `=` for a
yfinance alias when the public ticker differs from Yahoo's internal symbol
(e.g. `SPX=^SPX`).

```
SPX=^SPX
QQQ
IWM
MSFT
AMZN
NVDA
TLT
GLD
```

Add or remove freely; the workflow re-runs from this file each day.

## CSV format

One row per `(snapshot_date, expiration)`:

| Column | Meaning |
|---|---|
| `snapshot_date` | YYYY-MM-DD in America/New_York |
| `expiration` | YYYY-MM-DD |
| `dte` | days from snapshot_date to expiration |
| `spot` | underlying price at snapshot |
| `atm_strike` | strike closest to spot |
| `atm_iv` | average of call+put IV at the ATM strike (50Δ proxy) |
| `atm_call_iv` | call-side IV at the ATM strike |
| `atm_put_iv` | put-side IV at the ATM strike |
| `atm_call_oi` | open interest on the ATM call |
| `atm_put_oi` | open interest on the ATM put |

Files grow ~30 KB/ticker/year — git stays small forever.

## Manual back-fill

If you want to back-test, the snapshotter can only capture *today's* IV
from Yahoo's API — there's no public historical option-chain endpoint.
History builds forward from your first run. For backfill you'd need a
paid source (ORATS, Polygon, IVolatility) — see the main repo's planning
doc for that path.

## Troubleshooting

- **`SPX` returns no options.** Yahoo's index ticker for S&P options is
  `^SPX`; the alias in `tickers.txt` handles this. If Yahoo deprecates
  `^SPX`, try `^GSPC` (the index quote) or `SPX` (some plans expose
  options under the bare ticker).
- **Run failed, all tickers zero rows.** Yahoo rate-limited or returned
  empty payloads. The workflow exits non-zero so you'll see a red ❌ in
  Actions. Re-run manually; if it persists for a day, file a Yahoo
  Finance status check.
- **A specific ticker's IV is NaN.** Some illiquid contracts return
  `NaN` for `impliedVolatility`. The script silently skips them; the
  CSV just gets fewer rows for that day.
