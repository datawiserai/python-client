# D4-L laptop Python client handoff

Date: 2026-09-13

Hosted status: **NOT RUN**

This report covers the local, offline client upgrade authorised by the
single-file D4-L implementation brief. It does not claim a new hosted test,
database quota observation, deployment, or release.

## 1. Repository, input, and commits

- Repository: `/Users/jj/VSCode/python-client`
- Branch: `client-d4`
- Starting HEAD: `2839d7df72e2c05e5ef3a77739c3f5d77abe4491`
- Starting state: clean at the recorded D4-L baseline. Source edits visible
  later during this handoff were parallel D4-L implementation work, not
  pre-existing unrelated user changes.
- Authoritative input: the user-supplied “D4-L — Upgrade the Python client on
  the laptop” self-contained brief, which supersedes the earlier Studio brief.
- Reviewed implementation commit:
  `9e1b2dca2080ee476a10344ccdc40a77c23acaed` (`Implement D4 conditional
  client caching`). The follow-up report-finalization commit containing this
  exact record is the local branch HEAD. No remote operation was performed.
- Final working-tree state: clean after the report-finalization commit; there
  were no unrelated pre-existing changes to distinguish.

## 2. Public usage and preserved models

The public constructor is:

```python
Client(
    api_key,
    *,
    cache_dir=None,
    use_cache=True,
    refresh_cache=False,
    base_url="https://api.datawiser.ai/v1",
    timeout=30,
    manifest_ttl=300,
    cache_max_bytes=256 * 1024 * 1024,
    cache_max_entries=2_000,
)
```

`base_url` is explicit for an approved non-Production environment and includes
the `/v1` base path. The Production default remains
`https://api.datawiser.ai/v1`; the D3c Preview is never selected implicitly.
The API key remains a secret datawiser credential sent in `X-API-Key`.

The item methods and return models are preserved:

| Call | Return model |
|---|---|
| `free_float(ticker)` | `FreeFloat` |
| `free_float_events(ticker)` | `FreeFloatEvents` |
| `free_float_events_detail(ticker)` | `FreeFloatEventsDetail` |
| `shares_outstanding(ticker)` | `SharesOutstanding` |
| `reference(ticker)` | `Reference` |
| `universe(endpoint, refresh=False)` | `Universe` |

Existing model helpers such as `latest()`, iteration, `raw`, and
`to_dataframe()` remain available. `client.diagnostics` exposes bounded,
redacted client-side request/cache facts; it is not server quota evidence.

## 3. Conditional requests, identifiers, and cache

Shares-outstanding and reference calls no longer require a manifest lookup.
The exact original identifier is encoded as a single path segment and sent to
the API. With an intact cached candidate and usable opaque server ETag, the
next online GET sends ordinary `If-None-Match` for that same identifier. A 304
returns the validated cached representation; a 200 validates and atomically
replaces it. A missing ETag causes a future ordinary GET. An unusable 304 can
trigger at most one unconditional recovery GET, recorded in diagnostics.

Ticker, country, qualified-key, and case-sensitive immutable-ID semantics stay
server-owned. A cached alias is only a previous-response hint: it cannot elect
a current ticker owner, rewrite the online URL, supply a foreign fallback, or
override a denial. A payload ticker differing from the requested alias is not
alone an error; `securityId` supplies financial identity.

Cache schema v2 isolates canonical origin plus base path, a non-reversible
credential namespace, dataset, resolved security ID, server representation
ETag, and local schema. Paths are hashed, bodies and metadata are integrity
checked, writes are atomic and owner-only where supported, and deterministic
size/entry bounds apply. Old timestamp-only caches remain untouched and are
not trusted or migrated. There is no automatic offline or stale-on-error
authority.

Free-float and free-float-events retain their manifest-based resolution and
existing payload decoding. They make an unconditional online data GET because
conditional support is not assumed for those routes; cached metadata or data
is not current-access authority. Universe metadata uses a bounded TTL and
`refresh=True`; discovery is not proof of current paid access.

## 4. Tests, build, and isolated import

The pre-change baseline was **19 passed, 1 failed**. The failure was
`tests/test_free_float_events.py::test_full_build_fixture_pairs_expand_to_full_payload`:
it requires compact/full gzip fixtures from the sibling
`../datawiser-api/data/basic/free-float-events` directory, whose required files
were absent. This is a sibling private-fixture dependency, not a newly observed
D4-L regression.

- Final offline test: `/Users/jj/miniconda3/envs/py311/bin/python -m pytest -q`
  completed with **92 passed, 0 failed, 0 skipped in 2.36 seconds**.
- The sibling-fixture test was removed rather than skipped. Its regression
  coverage is now supplied by repository-owned synthetic compact/full decoder
  tests and public FF/FFE client tests. No final test reaches a sibling API
  checkout.
- Python checks: `compileall` passed; Python 3.9 grammar parsing of package,
  tests, and the later-smoke example passed; `git diff --check` passed. No
  separate lint or type-check configuration exists in this repository.
- Build: `/Users/jj/miniconda3/envs/py311/bin/python -m build` successfully
  produced `dist/datawiserai-1.1.0-py3-none-any.whl` and
  `dist/datawiserai-1.1.0.tar.gz`. Archive inspection found only the
  client package in the wheel and the expected client source, docs, examples,
  tests, and validation utility in the sdist. Runtime metadata contains only
  `requests>=2.28`; no backend/publisher dependency or private data is present.
- The first isolated build attempt could not resolve the public Hatchling
  dependency because sandbox DNS was disabled. The authorised rerun downloaded
  that build dependency and passed; this was an environment failure, not a
  package-build failure.
- Fresh install: a new Python 3.11 venv under
  `/private/tmp/datawiser-d4-install.uDycAI/venv` installed the wheel and its
  public dependency, Requests 2.34.2. The first sandboxed dependency install
  similarly failed DNS; the authorised rerun passed.
- Outside-repository import/smoke: from `/private/tmp`, with `PYTHONPATH`
  removed, the installed module resolved from the venv's `site-packages` and
  completed synthetic SO/reference 200→304 calls through a no-socket Requests
  adapter with the documented models and two cache returns. It used no sibling
  `datawiser-api`, developer path, or network endpoint.

## 5. Synthetic request measurements

Final measured SO counts: **100 data GETs, 50 synthetic 200 bodies, 50
synthetic 304 responses, zero per-item manifest calls, and 50 synthetic
billable bodies**.

Final measured reference counts: **100 data GETs, 50 synthetic 200 bodies, 50
synthetic 304 responses, zero per-item manifest calls, and 50 synthetic
billable bodies**.

For each deterministic 50-security/two-pass priority-dataset fixture, the
acceptance result is 100 data GETs, 50 synthetic 200 bodies, 50 synthetic 304s,
and zero per-item manifest calls. The combined result is 200 requests, 100
bodies and 100 304s. These are synthetic transport counters, not hosted
persisted quota measurements or a speed benchmark.

## 6. Minimum server support and release ordering

The client requires the HTTP API to authenticate `X-API-Key` before selecting
the authorised SO/reference representation, issue an opaque ETag on a 200, and
honour standard `If-None-Match` with an authorised 304. Redirects must not be
needed for protected data routes. Metadata compatibility currently relies on
the supported public `/v1/manifest/{dataset}/updates` dictionary entries with
durable `security_id`, `ticker`, and update fields; the client does not read
Storage, database tables, internal pointers, or internal forwarding headers.

Production should first run the D3c-compatible conditional/authentication and
metering behaviour. Only then should client 1.1.0 be released with its existing
Production default. The explicit Preview URL is reserved for the separately
authorised later smoke test; this task performs no deployment or release.

## 7. Precisely scoped public-manifest fixture gap

No complete captured current response for `GET /v1/manifest/{dataset}` was
provided. To add or verify enriched current country/qualified universe labels,
the remaining input is one redacted 200 response body and headers from that
public endpoint identifying the exact durable-security-ID field, exact current
country-label and qualified-identifier fields, and its ETag header. The client
does not infer those labels from ticker text, alias order, or the internal
publisher `format_version: 3` schema.

The established `/v1/manifest/{dataset}/updates` shape is sufficient for
legacy `Universe` fields and FF/FFE compatibility, and no ETag is assumed for
that updates route. This narrowly scoped fixture gap does not block direct
SO/reference requests, identifier pass-through, or cache isolation.

## 8. Prepared later laptop smoke test

After explicit hosted-test authorisation, from a fresh environment containing
the built wheel:

```bash
python examples/d4_later_smoke.py
```

The script reads the key at runtime from a private `getpass` prompt, accepts no
key argument, uses the exact approved D3c Preview URL including `/v1`, and
creates a new temporary cache. It requests AAPL shares outstanding twice, AAPL
reference twice, prints returned identities and diagnostics, then treats a
403/404 for AAPL-CA neutrally without falling back to U.S. AAPL. Importing the
script makes no requests.

**Hosted smoke test: NOT RUN.**
