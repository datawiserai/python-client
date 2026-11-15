# datawiserai

Official Python client for the [datawiser API](https://api.datawiser.ai).

Full documentation and API usage guide: [datawiser.ai/api](https://datawiser.ai/api)

Browse free current US equity snapshots: [free float and shares outstanding](https://datawiser.ai/free-data)

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

## Configuration and identifiers

The API key is a datawiser credential and should be treated as a secret. The
client sends it in `X-API-Key`. Production is the default; an explicit API root
can be selected for an approved environment:

```python
client = dw.Client(
    api_key="pk_live_...",
    base_url="https://api.datawiser.ai/v1",
    timeout=30,
)
```

`base_url` identifies both the origin and base path. It must not contain URL
userinfo, a query string, or a fragment. HTTPS is required except for localhost
and loopback URLs used by local contract tests.

For `shares_outstanding()` and `reference()`, the exact identifier supplied by
the caller is safely encoded as one URL path segment and passed to the server.
The client does not elect an owner from discovery metadata or rewrite a request
to a previously cached security ID.

| Identifier form | Server interpretation |
|---|---|
| `security_id` (case-sensitive) | Exact security |
| `ticker-country-security_id` | Exact evidenced qualified association |
| `ticker-security_id` | Evidenced U.S.-only convenience form |
| `ticker-country` | Current owner in that country |
| Bare `ticker` (optionally ending in `.json`) | Current U.S. owner only; no foreign fallback |

The returned payload's `ticker` can be a historical or source label. The
returned `security_id` is the financial identity and is not rejected merely
because the label differs from the requested alias. Free-float and free-float
events retain their established manifest-based identifier resolution and
payload decoding; because conditional support is not assumed for those routes,
their online data GET is unconditional.

## Caching

The client automatically caches responses under `~/.datawiserai/cache/`.
Shares-outstanding and reference calls do not fetch a manifest first. Every
default online call requests the original identifier and, when an intact
cached candidate has a server-issued ETag, sends that opaque value in
`If-None-Match`. A 304 returns the validated cached model without a second data
request. A 200 validates and atomically replaces the representation. If the
server omits an ETag, the next call is an ordinary full GET.

Cache version 2 separates API origin and base path, a one-way credential
namespace, dataset, resolved `security_id`, server ETag/content identity, and
local schema. It uses hashed path components, integrity checks, owner-only
permissions where supported, atomic replacement, and bounded size/entry
retention. Existing timestamp-only cache files are left untouched and are not
silently trusted or migrated.

```python
client = dw.Client(api_key="...", cache_dir="/tmp/dw_cache")
client = dw.Client(api_key="...", use_cache=False)
client = dw.Client(api_key="...", refresh_cache=True)
client = dw.Client(
    api_key="...",
    manifest_ttl=300,
    cache_max_bytes=128 * 1024 * 1024,
    cache_max_entries=2_000,
)

client.clear_cache()                 # clear this API/key namespace
client.clear_cache("free-float")     # clear one dataset in this namespace
```

The default bounds are 256 MiB and 2,000 entries per API/key namespace.
`use_cache=False` bypasses ordinary cache reads and writes; when combined with
`refresh_cache=True`, fresh results are still written. `refresh_cache=True`
forces an unconditional data fetch. Universe metadata is cached for
`manifest_ttl` seconds and can be explicitly refreshed with
`client.universe(endpoint, refresh=True)`. Free-float and free-float-events
continue using manifest resolution rather than the SO/reference ETag flow, but
cached metadata or data never substitutes for their default online data GET.

The cache is an efficiency mechanism, not access authority: the client does
not serve stale data after an online denial, service error, or network failure,
and it has no implicit offline mode. If a legitimate 304 arrives after its
cached body becomes unusable, the client makes at most one unconditional
recovery GET; `client.diagnostics` records that recovery because its returned
body may be metered.

## Errors

All client exceptions inherit from `DatawiserError`; HTTP failures retain
`DatawiserAPIError` compatibility and expose the status code. Typed subclasses
include `DatawiserAuthenticationError` (401), `DatawiserAccessError` (403),
`DatawiserNotFoundError` (404), `DatawiserRateLimitError` (429),
`DatawiserServiceError`, `DatawiserNetworkError`, and
`DatawiserResponseError`. A 404 means the requested identity or data is
unavailable to this caller; it does not prove global nonexistence. A 429
retains bounded retry metadata when supplied and is not retried automatically.
Cache/manifest compatibility failures use `DatawiserCacheError` and
`ManifestCompatibilityError`. The client does not fall back to cached paid data
or automatically retry a possibly metered data request after a timeout,
interrupted body, or decode failure.

## Free-float events file format

The client supports version 2 of the `free-float-events` file format. Version 2
uses a compact seed-plus-delta event stream instead of repeating a full
cumulative ownership snapshot on every event date. The first event in the file
seeds the mapping fields, and later events carry only changed mapping entries
plus explicit delete lists.

This format is marked in the payload as:

```json
{
  "eventFormat": "free_float_events_delta_v1",
  "eventSort": "as_of_ascending"
}
```

The SDK expands this compact stream internally, so `client.free_float_events()`
and `client.free_float_events_detail()` continue to return the same
client-facing cumulative event shape. The old version 1 full-snapshot payload
format is still accepted for compatibility with established responses and
fixtures, but it is deprecated and will be discontinued. This payload-format
compatibility does not read or migrate the pre-1.1 timestamp-only disk cache.

The version 2 format materially reduces transfer and cache size. In typical
exports it improves compression / file size by about **15x on average**.

## Free-float events validation tool

The repository includes a live API validation script at
[`validation/free_float_event_validation.py`](validation/free_float_event_validation.py).
It is intended for maintainers and schema reviewers who want to understand and
verify the `free-float-events` receiving format end to end.

Run it with a Datawiser API key:

```bash
export DATAWISER_API_KEY="pk_live_..."
python validation/free_float_event_validation.py --tickers AAPL
```

You can pass multiple comma-separated tickers, or omit `--tickers` to validate
the full `free-float-events` universe:

```bash
python validation/free_float_event_validation.py --tickers AAPL,MSFT,NVDA
python validation/free_float_event_validation.py --workers 4
```

The tool checks the manifest first, including recycled-ticker handling: duplicate
ticker entries must resolve to exactly one active non-delisted security, while
`-full` QC exports are ignored for active-security counting. It then fetches
`free-float-events` through the normal client and performs ground-up checks:

- `ff_factor` must recalculate from `excluded_shares` and `shares_out`.
- `delta_shares` must explain movement between adjacent event summary rows.
- excluded owner rows must sum to top-level `excluded_shares`.
- each owner's nested components must sum to that owner row's shares.
- nested components across all owners must sum to top-level `excluded_shares`.

These checks are intentionally stricter than a smoke test. They demonstrate how
the compact delta payload is expanded into the client-facing schema and prove
that the summary, flat owner view, and nested drill-down view reconcile.

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


### Example: pledged founder collateral and float treatment (ORCL)

A useful contrast to the TSLA examples is **Oracle (ORCL)**, where the proxy discloses that founder **Larry Ellison** has pledged a large block of shares as collateral for personal indebtedness. The proxy beneficial ownership table reports Ellison's total holdings and notes the pledged portion in a footnote. In contrast to the TSLA cases above, the key methodological question is whether those pledged shares should be considered part of free‑float.

Our methodology focuses first on whether a holder represents **founder or strategic control**. When a founder holds a large controlling block, we treat the entire position as excluded from free‑float regardless of whether some shares are pledged as collateral. Under this policy, Ellison's holdings in ORCL are treated as a strategic founder block and therefore excluded in full; the pledge disclosure is informative but does not change the float classification.

For comparison, Tesla (TSLA) includes proxy footnotes describing pledged collateral for Elon Musk. In our dataset those disclosures appear in the extracted **component notes** attached to the relevant owner components. The important distinction is that TSLA does not present a single controlling founder block in the same way as ORCL, so the treatment depends on the specific ownership mechanics disclosed in the filings rather than on a blanket founder-control rule.

Our approach therefore applies the same policy consistently: where a holder represents founder or strategic control (as with Ellison in ORCL) the entire block is excluded, whereas other insider-linked positions (such as those visible in TSLA filings) are evaluated based on the legal and economic characteristics described in the underlying disclosures. This framework also happens to produce results broadly consistent with how many major market data vendors classify ORCL versus TSLA.

You can still inspect the event stream in the same way as the TSLA examples:

```python
TICKER = "ORCL"

# Fetch event summary
summary = client.free_float_events(TICKER).to_event_summary_dataframe()
print(summary.head())

# Inspect detailed events
for ev in client.free_float_events_detail(TICKER)[:3]:
    print(ev.as_of, ev.delta)
```

In the ORCL case, changes in the excluded share numerator are more likely to come from ownership updates visible in proxy disclosures rather than from collateral status itself. This illustrates an important methodological distinction: **pledging may be visible in proxy footnotes, but float treatment often depends more on whether the holder is classified as a strategic founder or controlling insider.**

## Available endpoints

| Method | HTTP route | Returns |
|---|---|---|
| `client.free_float(ticker)` | `/v1/free-float/{identifier}` | `FreeFloat` |
| `client.free_float_events(ticker)` | `/v1/free-float-events/{identifier}` | `FreeFloatEvents` (flat) |
| `client.free_float_events_detail(ticker)` | `/v1/free-float-events/{identifier}` | `FreeFloatEventsDetail` (nested) |
| `client.shares_outstanding(ticker)` | `/v1/shares-outstanding/{identifier}` | `SharesOutstanding` |
| `client.reference(ticker)` | `/v1/reference/{identifier}` | `Reference` |
| `client.universe(endpoint, refresh=False)` | `/v1/manifest/{dataset}/updates` | `Universe` |

## Examples

See the [`examples/`](examples/) folder for runnable scripts and a Jupyter
notebook that walk through every endpoint:

- **[`quickstart.py`](examples/quickstart.py)** - command-line script
- **[`quickstart.ipynb`](examples/quickstart.ipynb)** - interactive notebook
- **[`d4_later_smoke.py`](examples/d4_later_smoke.py)** - later, manual hosted conditional-cache smoke test
- **[`company_relationships.py`](examples/company_relationships.py)** - company disclosures, local views and company reference

## Company relationships

The company-based paid dataset uses real, case-sensitive Datawiser company IDs:

```python
relationships = client.company_relationships("REAL_COMPANY_ID")

# Local views: these make no additional API requests.
suppliers = relationships.suppliers(status="current")
customers = relationships.customers(status="current")
competitors = relationships.competitors(directness="direct")
partners = relationships.partners()

# Filters combine and can be chained; omitted criteria keep every published value.
selected = relationships.filter(role="supplier", status="current", directness="direct")
assertions = selected.to_dataframe()
evidence = selected.evidence_dataframe()
metrics = selected.metrics_dataframe()

for assertion in selected:
    print(assertion.view_relationship_type, assertion.reporting_company.company_id)
    for metric in assertion.metrics:
        print(metric.value, metric.basis_company_id)  # Decimal; original basis

companies = client.universe("company-relationships")
print(companies.company_ids)
```

`CompanyRelationships` includes incoming and outgoing disclosures, source evidence,
and accepted financial metrics. `relationship_type` describes the original
reporter's counterparty; `view_relationship_type` describes the requested company's
counterparty. Reversing the view preserves the reporter and financial basis.
The convenience methods and `filter(role=...)` use `view_relationship_type`.
For example, a supplier's own disclosure naming the requested company as its
customer appears in that company's supplier view. `company_id` identifies the
requested company; `related_company_id` in the DataFrames consistently identifies
the other endpoint. Original reporter/counterparty IDs and names remain available.

Evidence/product text is untruncated. Latest-disclosed assertions can have current,
historical, planned, terminated or unknown status. Discovery returns a
`CompanyUniverse`, with company IDs and names, without requiring a listed security.
Tickers and website slugs are not accepted as company lookup aliases. Observation
IDs can change when upstream extraction is replaced. These are current published
disclosures; filtering dates/statuses does not reconstruct historical knowledge or
infer that a disclosed relationship is still active today.

Filters return new local views. `len(view)` counts assertions, and iteration yields
typed `CompanyRelationship` records. Chained filters narrow the existing view.
`selection` still describes the server's publication policy, while `raw` retains
the entire original server response, including assertions outside a local filter.

Install the pandas extra for DataFrame methods. The three views have explicit
grains and retain their column names even when empty:

| Method | One row per | Detail |
|---|---|---|
| `to_dataframe()` | Assertion | Focused/related companies, original roles, scope, status, dates and document provenance |
| `evidence_dataframe()` | Evidence passage | Full text, page/section/location, document URL and optional passage URL/tag |
| `metrics_dataframe()` | Accepted metric | Exact `Decimal` value, raw value, qualifier, currency, period and original basis company |

Multiple assertions about the same related company remain separate. Evidence and
metrics join on `relationship_id` and `evidence_id`; their DataFrames do not create
a passage-by-metric Cartesian product. Metric share fractions remain fractions,
and monetary values retain their published currency. Date columns retain ISO
strings. No automatic aggregation, float conversion or financial rebasing occurs.

Data calls use the existing authenticated conditional cache flow: cached data
never bypasses a server access check. Core and Index Intelligence both include
this dataset through explicit v2 rights on Basic storage; Enterprise access is
determined by the server. The SDK does not select plans or grant access. Filtering,
iteration and DataFrame conversion do not make requests. Fetching company reference
is a separate explicit request, with its own access check and potential quota use.

For a listed company, resolve its company ID through existing security reference:

```python
security = client.reference("NVDA")
if security.company_id is None:
    raise ValueError("Security reference did not supply a company ID")
relationships = client.company_relationships(security.company_id)
```

This adds one security-reference data request. Company discovery/direct IDs also
support private companies with no ticker. The SDK does not automatically fetch
reference records for every related company.

## Company reference

```python
reference = client.company_reference("REAL_COMPANY_ID")  # explicit supporting-data request
print(reference.company_id, reference.company_name, reference.lei)
for security in reference.securities:
    print(security.security_id, security.name, security.primary_ticker, security.security_type)

companies = client.universe("company-reference")
print(companies.company_ids)
```

`CompanyReference` returns current company name/LEI and zero or more eligible
`CompanyReferenceSecurity` records. Companies can have no eligible security; names,
LEIs, tickers and types can be absent. `to_dataframe()` returns one row per eligible
security. This method accepts exact Datawiser company IDs, without ticker or public
slug resolution. It uses a separate cache namespace from security reference and
relationships, with a server access/ETag check on each data call. Enterprise or an
explicit v2 `company-reference` right is required. Both Core and Index Intelligence
include it. Missing data and denied access retain the existing typed client errors;
an empty securities list is a valid company record.

The runnable [company example](examples/company_relationships.py) accepts either
`--company-id REAL_COMPANY_ID` or `--ticker NVDA`, plus an optional `--status`.
It fetches relationships and company reference explicitly, then explores local views.

## Repository

<https://github.com/datawiserai/python-client>
