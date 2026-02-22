# datawiserai

Official Python client for the [Datawiser API](https://api.datawiser.ai).

## Installation

```bash
pip install datawiserai

# with pandas support
pip install 'datawiserai[pandas]'
```

## Quick start

```python
import datawiserai as dw

client = dw.Client(api_key="pk_live_...")

# Discover available tickers for an endpoint
u = client.universe("free-float")
print(u.tickers)          # ['OLP', ...]
print("OLP" in u)         # True
print(u.to_dataframe())   # DataFrame of tickers, ids, timestamps

# Fetch free-float data
ff = client.free_float("OLP")
print(ff.latest())
df = ff.to_dataframe()

# Shares outstanding
so = client.shares_outstanding("OLP")
df = so.to_dataframe()

# Reference / identifier data
ref = client.reference("OLP")
print(ref.company_name, ref.cik)
print(ref.company_info)
print(ref.raw)            # full JSON payload

# Free-float events — high-level event summary (one row per date)
ffe = client.free_float_events("OLP")
df_events = ffe.to_event_summary_dataframe()
print(df_events.head())

# Free-float events — flat summary (one row per owner per date)
df = ffe.to_dataframe()

# Free-float events — full drill-down
detail = client.free_float_events_detail("OLP")
ev = detail[0]                          # first event date
ev.owner_names                          # {id: "Name", ...}
owner = ev.owner(ev.owner_ids[0])       # full nested dict
owner["components"]                     # sub-components list
owner["restrictions"]                   # restrictions list
owner["eventDetails"]                   # event details dict
```

## Caching

The client automatically caches responses under `~/.datawiserai/cache/`.
Before fetching data it checks the endpoint's **manifest** — if the
server-side `last_update` timestamp matches the cached copy, the local
version is returned instantly.

```python
client = dw.Client(api_key="...", cache_dir="/tmp/dw_cache")  # custom location
client = dw.Client(api_key="...", use_cache=False)             # disable

client.clear_cache()                # clear everything
client.clear_cache("free-float")    # clear one endpoint
```

## Free-float event summary: `is_rebal` (DEF 14A)

The high-level event summary DataFrame includes **`is_rebal`** (from the API's
`isRebalanced`). When **`is_rebal`** is `True`, the event corresponds to a
rebalance — in particular, the **new DEF 14A** proxy statement refresh, which
often updates beneficial-ownership and free-float.

```python
df_events = client.free_float_events("OLP").to_event_summary_dataframe()
rebal_dates = df_events[df_events["is_rebal"]]["as_of"]
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

- **[`quickstart.py`](examples/quickstart.py)** — command-line script
- **[`quickstart.ipynb`](examples/quickstart.ipynb)** — interactive notebook

## Repository

<https://github.com/datawiserai/python-client>
