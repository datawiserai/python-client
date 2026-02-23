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

# ── Free Float Events — detail drill-down (typed OwnerDetail) ──────────────────
print("=== Free Float Events (detail drill-down) ===")
detail = client.free_float_events_detail(TICKER)
print(f"event dates: {detail.dates[:3]}  ...")

ev = next(e for e in detail.events if e.is_rebal)
print(f"\nEvent {ev.as_of} — {len(ev.owner_ids)} owners:")
for oid, name in list(ev.owner_names.items())[:5]:
    owner = ev.owner(oid)  # OwnerDetail: .components, .restrictions, .options, .event_details
    delta = ev.owner_delta(oid) if ev.delta else {}
    print(
        f"  {owner.name:35s}  shares={owner.shares:>12,.2f}  "
        f"components={len(owner.components)}  "
        f"restrictions={len(owner.restrictions)}  "
        f"options={len(owner.options)}  "
        f"delta={delta}"
    )

# ── Extra: TSLA — large free-float move (2025-08-03) ───────────────────────────
print("\n=== Extra: TSLA — large free-float move (2025-08-03) ===")
df_tsla = client.free_float_events("TSLA").to_event_summary_dataframe()
large = df_tsla[df_tsla["delta_fff_bps"].abs() > 10]
print("Rows with |delta_fff_bps| > 10:")
print(df_tsla.loc[large.index[:3], ["as_of", "ff_factor", "delta_fff_bps", "delta_shares", "is_rebal"]].to_string())

detail_tsla = client.free_float_events_detail("TSLA")
ev_tsla = detail_tsla.by_date("2025-08-03")
print(f"\nDrill-down ev.delta for 2025-08-03: {ev_tsla.delta}")
owner_tsla = ev_tsla.owner("TSLATgVaxgiMn")
if owner_tsla.event_details:
    print(f"  owner.event_details.instrument_subtype: {owner_tsla.event_details.instrument_subtype}")
    print(f"  owner.event_details.notes: {(owner_tsla.event_details.notes or '')[:200]}...")
print()

# ── Extra: TSLA — CEO Performance Award restricted stock (2025-11-06) ─────────
print("=== Extra: TSLA — CEO Performance Award (2025-11-06) ===")
ev_tsla2 = detail_tsla.by_date("2025-11-06")
print(f"Event {ev_tsla2.as_of}: delta_ff_factor={ev_tsla2.delta_ff_factor:.6f}  delta_shares={ev_tsla2.delta_shares:,.0f}")
owner_tsla2 = ev_tsla2.owner("TSLATgVaxgiMn")
if owner_tsla2.event_details:
    print(f"  instrument_subtype: {owner_tsla2.event_details.instrument_subtype}")
    print(f"  notes (first 300 chars): {(owner_tsla2.event_details.notes or '')[:300]}...")
print()

# ── Extra: AMZN — MacKenzie Scott (rebalance) ──────────────────────────────────
print("=== Extra: AMZN — MacKenzie Scott (rebalance) ===")
detail_amzn = client.free_float_events_detail("AMZN")
ev_amzn = next((e for e in detail_amzn.events if e.is_rebal), None)

owner_ms = ev_amzn.owner_from_name("MacKenzie Scott")
oid_ms = owner_ms.owner_identity_id
print(f"{owner_ms.name:35s}  shares={owner_ms.shares:>12,.2f}  components={len(owner_ms.components)}  delta={ev_amzn.owner_delta(oid_ms)}")

pass
