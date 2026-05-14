"""Daily ATM-IV snapshotter.

For each ticker in tickers.txt, fetch every available option expiration from
yfinance and record the 50-delta (ATM) IV. One CSV per ticker under data/.
Idempotent on (snapshot_date, expiration) — re-running on the same day
overwrites that day's rows.

Designed to run from GitHub Actions once a day after the US market close.
No credentials required; yfinance hits Yahoo's public API.
"""
from __future__ import annotations

import csv
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
TICKERS_FILE = ROOT / "tickers.txt"
NY = ZoneInfo("America/New_York")

CSV_COLUMNS = (
    "snapshot_date",
    "expiration",
    "dte",
    "spot",
    "atm_strike",
    "atm_iv",
    "atm_call_iv",
    "atm_put_iv",
    "atm_call_oi",
    "atm_put_oi",
)


def load_tickers() -> list[tuple[str, str]]:
    """Return list of (logical_symbol, yf_symbol). Logical is the column name
    used for the CSV filename; yf_symbol is what we pass to yf.Ticker()."""
    out: list[tuple[str, str]] = []
    for line in TICKERS_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            logical, _, yf_sym = line.partition("=")
            out.append((logical.strip().upper(), yf_sym.strip()))
        else:
            sym = line.strip().upper()
            out.append((sym, sym))
    return out


def snapshot_date_iso() -> str:
    return datetime.now(NY).date().isoformat()


def get_spot(t: yf.Ticker) -> float:
    """Most reliable spot extraction across yfinance versions."""
    try:
        info = t.fast_info
        for k in ("lastPrice", "last_price", "regularMarketPrice", "previousClose"):
            v = getattr(info, k, None) if not isinstance(info, dict) else info.get(k)
            if v and float(v) > 0:
                return float(v)
    except Exception:
        pass
    try:
        hist = t.history(period="5d", auto_adjust=False)
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return 0.0


def snapshot_ticker(logical: str, yf_symbol: str, snap_date: str) -> list[dict]:
    print(f"  → {logical} (yf={yf_symbol})")
    t = yf.Ticker(yf_symbol)
    spot = get_spot(t)
    if spot <= 0:
        print(f"     no spot price — skipping")
        return []

    try:
        expirations: tuple[str, ...] = t.options
    except Exception as exc:
        print(f"     no options listing: {exc}")
        return []
    if not expirations:
        print(f"     empty options listing")
        return []

    today = date.fromisoformat(snap_date)
    rows: list[dict] = []
    for exp_str in expirations:
        try:
            exp_d = date.fromisoformat(exp_str)
        except ValueError:
            continue
        dte = (exp_d - today).days
        if dte < 0:
            continue

        try:
            chain = t.option_chain(exp_str)
        except Exception as exc:
            print(f"     {exp_str} chain failed: {exc}")
            continue

        calls = chain.calls if hasattr(chain, "calls") else pd.DataFrame()
        puts = chain.puts if hasattr(chain, "puts") else pd.DataFrame()
        if calls.empty and puts.empty:
            continue

        # ATM = strike closest to spot, considering both sides.
        all_strikes = sorted(set(
            list(calls["strike"]) if not calls.empty else []
        ) | set(
            list(puts["strike"]) if not puts.empty else []
        ))
        if not all_strikes:
            continue
        atm_strike = min(all_strikes, key=lambda k: abs(float(k) - spot))

        call_iv = call_oi = put_iv = put_oi = None
        if not calls.empty:
            row = calls[calls["strike"] == atm_strike]
            if not row.empty:
                iv = row["impliedVolatility"].iloc[0]
                oi = row["openInterest"].iloc[0] if "openInterest" in row else None
                if pd.notna(iv) and float(iv) > 0.01:
                    call_iv = float(iv)
                if oi is not None and pd.notna(oi):
                    call_oi = int(oi)
        if not puts.empty:
            row = puts[puts["strike"] == atm_strike]
            if not row.empty:
                iv = row["impliedVolatility"].iloc[0]
                oi = row["openInterest"].iloc[0] if "openInterest" in row else None
                if pd.notna(iv) and float(iv) > 0.01:
                    put_iv = float(iv)
                if oi is not None and pd.notna(oi):
                    put_oi = int(oi)

        if call_iv is None and put_iv is None:
            continue
        ivs = [v for v in (call_iv, put_iv) if v is not None]
        atm_iv = sum(ivs) / len(ivs)

        rows.append({
            "snapshot_date": snap_date,
            "expiration": exp_str,
            "dte": dte,
            "spot": round(spot, 4),
            "atm_strike": round(float(atm_strike), 4),
            "atm_iv": round(atm_iv, 6),
            "atm_call_iv": round(call_iv, 6) if call_iv is not None else "",
            "atm_put_iv": round(put_iv, 6) if put_iv is not None else "",
            "atm_call_oi": call_oi if call_oi is not None else "",
            "atm_put_oi": put_oi if put_oi is not None else "",
        })

    print(f"     {len(rows)} expirations, spot=${spot:.2f}")
    return rows


def upsert_csv(logical: str, new_rows: Iterable[dict]) -> int:
    """Merge new_rows into data/{logical}.csv with (snapshot_date, expiration)
    as the unique key. Returns row count after merge."""
    new_rows = list(new_rows)
    if not new_rows:
        return 0
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{logical}.csv"

    if path.exists():
        existing = pd.read_csv(path, dtype=str).fillna("")
    else:
        existing = pd.DataFrame(columns=list(CSV_COLUMNS))

    new_df = pd.DataFrame(new_rows, columns=list(CSV_COLUMNS)).astype(str).fillna("")
    combined = pd.concat([existing, new_df], ignore_index=True)
    combined = combined.drop_duplicates(
        subset=["snapshot_date", "expiration"], keep="last"
    )
    combined = combined.sort_values(["snapshot_date", "expiration"])
    combined.to_csv(path, index=False)
    return len(combined)


def main() -> int:
    snap_date = snapshot_date_iso()
    print(f"IV snapshot for {snap_date} (America/New_York)")
    tickers = load_tickers()
    if not tickers:
        print("No tickers configured in tickers.txt")
        return 1

    summary: list[tuple[str, int, int]] = []
    failures = 0
    for logical, yf_sym in tickers:
        try:
            rows = snapshot_ticker(logical, yf_sym, snap_date)
        except Exception as exc:
            print(f"     ERROR for {logical}: {exc}")
            failures += 1
            continue
        if not rows:
            summary.append((logical, 0, 0))
            continue
        total = upsert_csv(logical, rows)
        summary.append((logical, len(rows), total))

    print("\nSummary:")
    for logical, new_rows, total in summary:
        print(f"  {logical:6} +{new_rows:3} rows  ({total} total)")
    if failures:
        print(f"\n{failures} ticker(s) failed — see log above.")
    if all(s[1] == 0 for s in summary):
        print("\nNo data captured — failing the run so the workflow surfaces it.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
