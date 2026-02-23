# datawiserai

Official Python client for the [datawiser API](https://api.datawiser.ai).

Full documentation and API usage guide: [datawiser.ai/api](https://datawiser.ai/api)

## Installation

```bash
pip install datawiserai

# with pandas support
pip install 'datawiserai[pandas]'
```

## Quick start

```python
import datawiserai as dw

TICKER = "OLP"
client = dw.Client(api_key="pk_live_...")

# Discover available tickers for an endpoint
u = client.universe("free-float")
print(u.tickers)          # ['OLP', ...]
print(TICKER in u)       # True
print(u.to_dataframe())   # DataFrame of tickers, ids, timestamps

# Fetch free-float data
ff = client.free_float(TICKER)
print(ff.latest())
df = ff.to_dataframe()

# Shares outstanding
so = client.shares_outstanding(TICKER)
df = so.to_dataframe()

# Reference / identifier data
ref = client.reference(TICKER)
print(ref.company_name, ref.cik)
print(ref.company_info)
print(ref.raw)            # full JSON payload

# Free-float events — high-level event summary (one row per date)
ffe = client.free_float_events(TICKER)
df_events = ffe.to_event_summary_dataframe()
print(df_events.head())

# Free-float events — flat summary (one row per owner per date)
df = ffe.to_dataframe()

# Free-float events — full drill-down (typed OwnerDetail)
detail = client.free_float_events_detail(TICKER)
ev = detail[0]                          # FreeFloatEventDetail
ev.owner_names                          # {id: "Name", ...}
owner = ev.owner(ev.owner_ids[0])       # OwnerDetail
owner.components                        # List[Component]
owner.restrictions                      # List[Restriction]
owner.options                           # List[Option]
owner.event_details                     # EventDetails or None
```

## Caching

The client automatically caches responses under `~/.datawiserai/cache/`.
Before fetching data it checks the endpoint's **manifest** - if the
server-side `last_update` timestamp matches the cached copy, the local
version is returned instantly.

```python
client = dw.Client(api_key="...", cache_dir="/tmp/dw_cache")  # custom location
client = dw.Client(api_key="...", use_cache=False)             # disable

client.clear_cache()                # clear everything
client.clear_cache("free-float")    # clear one endpoint
```

## Free-float event summary: `is_rebal`

The high-level event summary DataFrame includes **`is_rebal`** (from the API's `isRebalanced`). Our pipeline is event-sourced: we ingest and process changes continuously from daily/near-daily filings such as Forms 3/4/5 (and related corporate-action signals), so most dates reflect incremental updates.

When **`is_rebal`** is `True`, that date is a scheduled reconciliation checkpoint - typically the annual DEF 14A proxy refresh where beneficial ownership is re-stated in one place. We use it to "rebalance" the running history (i.e. validate that the accumulated daily events still reconcile to the ownership snapshot implied by the proxy).

In an ideal world, if every prior event has been captured and processed correctly, the `is_rebal` date would introduce *no* material change (delta) because the series is already consistent. In practice this is not always possible: filings can be late, amended, ambiguous, missing for edge cases, or require manual interpretation; and some ownership changes are only clearly disclosed when the proxy is published.

Our goal is that any reconciliation adjustment is small - typically we aim for <50 bps deviation in free-float factor on the rebalance date. In early coverage, the distribution is usually much tighter; for example, a practical target is that the p90 rebalance adjustment stays well below that 50 bps budget. The rebalance checkpoint below shows **-0.027 bps** (effectively zero), meaning the daily event stream had already "predicted" the proxy snapshot.

For more information on how free-float events are detected, reconciled, and audited, see the methodology page: https://datawiser.ai/docs/methodology/free-float-events

```text
=== Free Float Events (event summary) ===
        as_of  delta_fff_bps  is_rebal  ff_factor
0  2026-01-14     -86.222216    False   0.725915
1  2025-09-09       2.526722    False   0.732174
2  2025-09-05       3.032832    False   0.731989
3  2025-09-04       1.175239    False   0.731766
4  2025-09-03       2.706101    False   0.731680
...
13 2025-03-19      -0.027326     True   0.731905
```


A useful real-world edge case is a private holder whose position only becomes visible at proxy time (i.e. no Forms 3/4/5 trail). For example, Amazon's proxy disclosures can surface shares held by Mr. Bezos' ex-wife, MacKenzie Scott via a footnote that effectively splits voting vs investment power ("Includes XXX shares as to which Mr. Bezos has sole voting power and no investment power"). As a private citizen who is not an officer or director (and is below the Section 16 10% threshold), she has no ongoing obligation to file Forms 3/4/5, so day-to-day sells will not be visible in insider filings. Despite that, these shares are typically treated as excluded from free-float because the proxy footnote ties the voting power to Mr. Bezos - i.e. the position is economically separate but remains linked to insider control. In our event stream this appears as a larger annual proxy reconciliation - here **~75.8 bps** on the rebalance date - but the drill-down shows exactly which owner moved, by how much, and why it is expected that the change is only observable at proxy time.

```text
         as_of  excluded_shares  ff_factor   delta_shares  delta_ff_factor  delta_fff_bps  is_rebal     shares_out
49  2025-03-03   1024907994.996   0.903290    -242343.004         0.000023       0.254625     False  10597729352.0
50  2025-02-24   1025150338.000   0.903267  -72560497.901         0.006847      75.802614      True  10597729352.0
51  2025-02-21   1097869736.901   0.896405      23942.946        -0.000002      -0.022311     False  10597729352.0
```

```python
# Example drill-down: identify the owner by name and inspect the delta source
ev = client.free_float_events_detail("AMZN")[0]  # FreeFloatEventDetail

owner = ev.owner_from_name("MacKenzie Scott")
oid = owner.id

print(
    f"{owner.name:35s}  shares={owner.shares:>12,.2f}  "
    f"components={len(owner.components)}  "
    f"delta={ev.owner_delta(oid)}"
)
```

```text
 MacKenzie Scott  shares=112,032,131.00  components=1 delta={'diff': -72250000.0, 'src': 'proxystatement|2024-04-11|AMZ3JVbI_rq|AMZ3JVbI_rq->proxystatement|2025-04-10|AMZ3JB_Pkym|AMZ3JB_Pkym'}
```

This kind of attribution (owner-level delta + provenance across specific source filings) is what makes the event stream auditable - even when the underlying disclosure only appears in an annual proxy footnote.

```python
import pandas as pd

TICKER = "OLP"
df_events = client.free_float_events(TICKER).to_event_summary_dataframe()

# Keep the sample readable: show latest rows plus the reconciliation checkpoint(s)
cols = ["as_of", "delta_fff_bps", "is_rebal", "ff_factor"]
latest = df_events.sort_values("as_of", ascending=False).head(5)
rebal = df_events[df_events["is_rebal"]]
print(pd.concat([latest, rebal]).sort_values("as_of", ascending=False)[cols].to_string(index=False))
```

### Example: large free-float move from a restricted stock award (TSLA)

Large `delta_fff_bps` values usually indicate a discrete ownership change that materially affects the excluded-share numerator.

The snippet below finds dates where the free-float factor moved by more than 10 bps, then inspects the largest move. In this example the free-float factor drops by ~3.5% (-354 bps) on 2025-08-03.

```python
# Find large moves and inspect the largest one
TICKER = "TSLA"

df_events = client.free_float_events(TICKER).to_event_summary_dataframe()
large = df_events[df_events["delta_fff_bps"].abs() > 10]
print(df_events.loc[[n + i for n in large.index for i in (0, 1)]].to_string())

# Drill down the largest move
detail = client.free_float_events_detail(TICKER)
ev = detail.by_date('2025-08-03')
print(ev.delta)
```

```text
         as_of  excluded_shares  ff_factor  delta_shares  delta_ff_factor  delta_fff_bps  is_rebal    shares_out
11  2025-08-03      510618229.0   0.841691    96000000.0        -0.029763    -353.609579     False  3225448889.0
12  2025-07-11      414618229.0   0.871275       90000.0        -0.000028      -0.321368     False  3220956211.0
```

The `delta` payload shows that the change is attributable to a single owner and a single filing-to-filing provenance chain:

```text
{'diff': {'TSLATgVaxgiMn': 96000000.0}, 'src': {'TSLATgVaxgiMn': 'form4|2024-12-31|1318605/0000950170-24-141705|TSLbIK5y4Jh->form4|2025-08-04|1318605/0001104659-25-073753|TSLbIcl17Ko'}}
```

If you drill into that owner for the event date, the extracted `event_details` explains why the position is treated as excluded:

```python
owner = ev.owner("TSLATgVaxgiMn")
print(owner.event_details.instrument_subtype)
print(owner.event_details.notes)
```

```text
restricted_stock
instrument_subtype set to 'restricted_stock' based on footnotes describing a restricted stock award to be delivered upon vesting; EXPORTED_TRANSACTION_CONTEXT.sec_type='Common Stock'. | settlement_type left null because delivery is deferred and not explicitly settled in the filing. | shares granted (delta) not provided in the exported context; vesting_schedule includes the known vesting date but delta_shares omitted. | ownership_nature not specified in filing; defaulted to 'unknown' per resolution guidelines.
```

Methodology note: classification is inherently subjective and index vendors differ on what counts as investable. Our treatment is systematic: when a filing indicates **legally issued stock** (e.g. restricted stock issued as Common Stock) and **voting rights or voting control exist** (including via voting agreements / proxies), we treat those shares as **excluded from free-float** because they represent insider-controlled equity that is not freely investable. By contrast, **RSUs** are typically not counted as excluded until they are settled or otherwise impact shares outstanding. Because vendor rules vary, you may see some vendors keep free-float closer to the pre-event level (effectively including restricted stock) while others exclude it (aligning with our lower free-float).

Coming soon: our **Shares Outstanding Events** product will make these cases easier to interpret by explicitly event-sourcing the denominator as well, so you can see whether an equity award affected the numerator, the denominator, or both.


#### Additional TSLA example: CEO Performance Award restricted stock issuance (2025-11-06)

A second TSLA example shows why an event-sourced stream can be predictive. On 2025-11-06, a restricted stock issuance increases excluded shares by ~423.7M and reduces the free-float factor by ~17.7% (a huge **-1772 bps**) on that date:

```text
        as_of  excluded_shares  ff_factor  delta_shares  delta_ff_factor  delta_fff_bps  is_rebal    shares_out
5  2025-11-06      935050609.0   0.718851  4.237439e+08        -0.127410   -1772.411807     False  3.325819e+09
6  2025-09-15      511306705.0   0.841477 -5.606775e+04         0.000017       0.202026      True  3.225449e+09
```

At time of writing, it is too soon to know how every downstream vendor will classify and incorporate this award. The earliest we would typically expect reconciliation is around March 2026, but for many methodologies the more meaningful “catch-up” may only appear in the larger semi-annual rebalance windows (more likely May/June). Some vendors may include restricted stock (keeping free-float closer to the pre-event level), while others exclude it. The key point is that the event stream captures the change immediately from the underlying filing(s), so when vendors reconcile at their next methodology checkpoint / rebalance cycle, it provides a clear, auditable prediction of the direction and magnitude they may converge toward.

Speculative assessment: based on how vendors often handle judgment calls, S&P is unlikely to treat these shares as excluded (not impacting the free-float factor), whereas FTSE, which tends to apply more systematic rule-based treatment, may be more likely to exclude them (impacting free-float factor) - though the final outcome depends on each vendor’s specific interpretation of “investable” and the exact mechanics disclosed in the filings.


```python
# Drill-down for the 2025-11-06 event
ev = detail.by_date('2025-11-06')
owner = ev.owner("TSLATgVaxgiMn")
print(owner.event_details.instrument_subtype)
print(owner.event_details.notes)
```


## Available endpoints

| Method | Endpoint | Returns |
|---|---|---|
| `client.free_float(ticker)` | `/v1/free-float/{ticker}` | `FreeFloat` |
| `client.free_float_events(ticker)` | `/v1/free-float-events/{ticker}` | `FreeFloatEvents` (flat) |
| `client.free_float_events_detail(ticker)` | `/v1/free-float-events/{ticker}` | `FreeFloatEventsDetail` (nested) |
| `client.shares_outstanding(ticker)` | `/v1/shares-outstanding/{ticker}` | `SharesOutstanding` |
| `client.reference(ticker)` | `/v1/reference/{ticker}` | `Reference` |
| `client.universe(endpoint)` | `/v1/{endpoint}/manifest` | `Universe` |

## Examples

See the [`examples/`](examples/) folder for runnable scripts and a Jupyter
notebook that walk through every endpoint:

- **[`quickstart.py`](examples/quickstart.py)** - command-line script
- **[`quickstart.ipynb`](examples/quickstart.ipynb)** - interactive notebook

## Repository

<https://github.com/datawiserai/python-client>
