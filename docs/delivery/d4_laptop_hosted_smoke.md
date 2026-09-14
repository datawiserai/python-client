# D4-H installed-client hosted smoke

Date: 2026-09-14

Outcome: **PASS**. The installed client completed the bounded customer test
against the exact approved Preview. No Production, provider, Storage,
database, billing-administration, deployment, release, or Git remote action
occurred.

## 1. Client and installed artifact

- Branch: `client-d4`
- Pre-test HEAD: `972fa20c44ebb2b607b0f4bfdc515021c7af5931`
  (`Finalize D4 laptop handoff`)
- Reviewed runtime commit:
  `9e1b2dca2080ee476a10344ccdc40a77c23acaed`
  (`Implement D4 conditional client caching`)
- Wheel: `datawiserai-1.1.0-py3-none-any.whl`
- Wheel SHA-256:
  `1ade69e3c9565682c7d045abb4c396ef4ad8e893b404f2af9fc04d0433295e10`
- Installed version: `1.1.0`
- Installed module:
  `/private/tmp/datawiser-d4-install.uDycAI/venv/lib/python3.11/site-packages/datawiserai/__init__.py`
- Python: `3.11.5`
- Requests: `2.34.2`

The run started in `/private/tmp` with `PYTHONPATH` removed. The installed
package was byte-compared with the reviewed source before execution. The
active development environment's unrelated editable-install metadata was not
used as hosted evidence.

## 2. Hosted target and controls

- Exact base URL:
  `https://datawiser-m7pbzbu6l-datawisers-projects.vercel.app/v1`
- Hosted execution: **OCCURRED**
- Active hosted duration: about `13.99` seconds
- Total decoded response material: `7,563` bytes
- One fresh private cache, one client, and one Requests session were used for
  all seven calls.
- Timeout was 30 seconds per call, automatic retries were zero, redirects were
  disabled, and the harness had an independent five-minute alarm.
- The key was copied in memory from the user-designated local
  `DATAWISER_API_KEY_FREE` field into the protected designated input. Its value
  was never printed, placed in a process argument, or committed.

## 3. HTTP request ledger

`If-None-Match` below describes the outgoing standard validator. “Cached
model” means the SDK returned its intact cached representation after the
observed 304. No row involved a redirect or an extra recovery request.

| # | Kind | Safe path | HTTP | If-None-Match | Response ETag | Cached model | Security ID | Extra request |
|---:|---|---|---:|---|---|---|---|---|
| 1 | SDK SO first | `/v1/shares-outstanding/AAPL` | 200 | Absent | Present | No | `AAPLRkNMQu0` | No |
| 2 | SDK SO second | `/v1/shares-outstanding/AAPL` | 304 | Present | Present | Yes | `AAPLRkNMQu0` | No |
| 3 | SDK reference first | `/v1/reference/AAPL` | 200 | Absent | Present | No | `AAPLRkNMQu0` | No |
| 4 | SDK reference second | `/v1/reference/AAPL` | 304 | Present | Present | Yes | `AAPLRkNMQu0` | No |
| 5 | SDK reference denial | `/v1/reference/AAPL-CA` | 404 | Absent | Absent | No | — | No |
| 6 | Metadata capture | `/v1/manifest/shares-outstanding` | 200 | Absent | Present | No | — | No |
| 7 | Metadata capture | `/v1/manifest/reference` | 200 | Absent | Present | No | — | No |

The first five rows completed before either metadata request. Client
diagnostics recorded five data requests, two conditional requests, two 304s,
two cached-304 returns, zero unconditional recoveries, zero missing-ETag
responses, zero manifest prerequisites, and zero cache-write failures.

## 4. Model and body checks

- AAPL SO decoded-body SHA-256:
  `7900a8b4b9f96aa522f96e72fd651b40b8128c761111f4c2fd51c26175d46b0e`
- AAPL reference decoded-body SHA-256:
  `080536e222f90c5e3d306c8a1b2e81b3276c8355d848e9583e0a6508ab9db7de`
- Both hashes exactly matched the D3c expected values. They were calculated
  from Requests' once-decoded bytes on the same 200 responses; no second
  download, reserialization, or second decompression occurred.
- SO returned `AAPLRkNMQu0` with 27 events. `latest()`, `len()`, and iteration
  worked. The first and cached-after-304 SO models were equal.
- Reference returned `AAPLRkNMQu0`; its typed fields and `raw` dictionary were
  usable. The first and cached-after-304 Reference models and raw dictionaries
  were equal.
- `reference("AAPL-CA")` raised the typed 404 unavailable response. It returned
  no US AAPL body and used no cached fallback.

## 5. Request totals and quota boundary

| Measurement | Observed |
|---|---:|
| Total HTTP requests | 7 |
| Successful data bodies | 2 |
| Conditional 304 responses | 2 |
| Unavailable-country denials | 1 |
| Explicit metadata calls | 2 |
| Recovery calls | 0 |

The first SO 200 carried `X-RateLimit-Limit: 10000` and
`X-RateLimit-Remaining: 9970`; the reference 200 carried the same limit and
remaining `9969`. The 304s did not carry those fields. These are captured
response headers, not an independent database counter observation. Persisted
quota delta was **not independently measured** by this laptop test.

## 6. Public manifest fields and private fixtures

Both full-manifest bodies have exactly these root fields:

- `dataset`
- `generated_at`
- `tickers`

For shares-outstanding, `tickers` contains six entries; for reference, nine.
Despite its name, this field is a dictionary keyed by immutable security ID:
the expected AAPL ID `AAPLRkNMQu0` is a key in both captures. Each corresponding
value is an object with only `etag` and `last_modified`.

Neither endpoint supplies an explicit current-ticker field, country field, or
qualified-key field. Therefore the captures verify immutable security identity
and freshness metadata, but do not close the enriched country/current-label
gap. No labels were inferred from punctuation, insertion order, ID prefixes,
or internal schemas.

The allowlisted response headers present on both metadata calls were `ETag`,
`Content-Type: application/json`, `Cache-Control: private, max-age=60`, and
`Vary: X-API-Key`; `Content-Encoding` was absent.

Private fixture directory, mode 0700:

`/var/folders/1j/4kjl475s7d18qw_7svgrr27h0000gn/T/datawiserai-d4h-96ibuowc/manifest-fixtures`

It contains owner-only 0600 decoded bodies and allowlisted-header files:

- `shares-outstanding.decoded.json`
- `shares-outstanding.headers.json`
- `reference.decoded.json`
- `reference.headers.json`

The safe request ledger is privately preserved at:

`/var/folders/1j/4kjl475s7d18qw_7svgrr27h0000gn/T/datawiserai-d4h-96ibuowc/result.json`

## 7. Defects, limitations, and local validation

No installed-client runtime defect was observed, and the reviewed runtime was
not modified. Before the hosted call, the existing example harness was
narrowly corrected to use `reference("AAPL-CA")`, observe actual statuses and
validators, enforce the request/body/time bounds, assert model and hash
results, and save only the approved private metadata. Its focused offline suite
passes nine tests, including the complete seven-request contract and the
single-recovery stop path.

The only remaining concrete limitation is the absent public-manifest label
fields described above. It does not block the successful SO/reference cache
test.

## 8. Commits and working tree

The D4-H harness, focused tests, and initial report are committed locally as
`1974c948e8615a87a2844920f5c33db35cdb3edc` (`Validate installed D4 client
against Preview`). The follow-up documentation-only commit containing this
exact record is branch HEAD. The final working tree is clean. No push, merge,
package rebuild, PyPI publication, deployment, or Production-default change
was performed.
