"""Datawiser API — quickstart example.

Usage:
    pip install 'datawiserai[pandas]'
    export DATAWISER_API_KEY="pk_live_..."
    python quickstart.py
"""

import os

import datawiserai as dw

API_KEY = os.environ["DATAWISER_API_KEY"]
TICKER = "OLP"

client = dw.Client(api_key=API_KEY)

# ── Universe ──────────────────────────────────────────────────────────────────
print("=== Universe (free-float) ===")
u = client.universe("free-float")
print(f"tickers: {u.tickers}")
print(f"'{TICKER}' available: {TICKER in u}")
print()

# ── Free Float ────────────────────────────────────────────────────────────────
print("=== Free Float ===")
ff = client.free_float(TICKER)
print(f"ticker={ff.ticker}  events={len(ff)}")
latest = ff.latest()
print(f"latest: {latest.as_of}  factor={latest.free_float_factor:.4f}")
print(ff.to_dataframe().head().to_string())
print()

# ── Shares Outstanding ───────────────────────────────────────────────────────
print("=== Shares Outstanding ===")
so = client.shares_outstanding(TICKER)
print(f"ticker={so.ticker}  events={len(so)}")
latest_so = so.latest()
print(f"latest: {latest_so.as_of}  type={latest_so.share_type}  shares={latest_so.shares:,.0f}")
print(so.to_dataframe().head().to_string())
print()

# ── Reference ────────────────────────────────────────────────────────────────
print("=== Reference ===")
ref = client.reference(TICKER)
print(f"ticker={ref.ticker}  company={ref.company_name}")
print(f"cik={ref.cik}  lei={ref.lei}  mic={ref.mic}")
print(f"company_info: {ref.company_info}")
print(f"security_info: {ref.security_info}")
print()

# ── Free Float Events — high-level event summary ─────────────────────────────
print("=== Free Float Events (high-level event summary) ===")
ffe = client.free_float_events(TICKER)
df_events = ffe.to_event_summary_dataframe()
cols = ["as_of", "excluded_shares", "ff_factor", "delta_shares",
        "delta_ff_factor", "delta_fff_bps", "is_rebal", "shares_out"]
print(df_events[cols].head(10).to_string())
print()

# ── Free Float Events — flat owner summary ───────────────────────────────────
print("=== Free Float Events (flat summary) ===")
print(f"ticker={ffe.ticker}  owner-rows={len(ffe)}  dates={len(ffe.dates())}")
df_ffe = ffe.to_dataframe()
print(df_ffe[["as_of", "name", "shares", "delta_shares", "entity_type"]].head(10).to_string())
print()

# ── Free Float Events — detail drill-down ────────────────────────────────────
print("=== Free Float Events (detail drill-down) ===")
detail = client.free_float_events_detail(TICKER)
print(f"event dates: {detail.dates[:3]}  ...")

ev = detail[0]
print(f"\nEvent {ev.as_of} — {len(ev.owner_ids)} owners:")
for oid, name in list(ev.owner_names.items())[:5]:
    owner = ev.owner(oid)
    print(
        f"  {name:35s}  shares={owner['shares']:>12,.2f}  "
        f"components={len(owner.get('components', []))}  "
        f"restrictions={len(owner.get('restrictions', []))}"
    )
