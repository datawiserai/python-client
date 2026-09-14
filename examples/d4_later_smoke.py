"""Bounded D4-H customer smoke test against the approved D3c Preview.

This script makes hosted, potentially metered requests. Importing it performs
no I/O and makes no network calls. It must be run with an installed wheel from
outside the source tree and with developer ``PYTHONPATH`` removed.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import platform
import signal
import stat
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import datawiserai
import requests
from datawiserai import (
    Client,
    DatawiserAPIError,
    DatawiserAccessError,
    DatawiserNotFoundError,
)


APPROVED_PREVIEW_BASE_URL = (
    "https://datawiser-m7pbzbu6l-datawisers-projects.vercel.app/v1"
)
DESIGNATED_KEY_FILE = Path.home() / ".config/datawiser/client-d4-smoke.env"
EXPECTED_SECURITY_ID = "AAPLRkNMQu0"
EXPECTED_SO_EVENTS = 27
EXPECTED_SO_SHA256 = (
    "7900a8b4b9f96aa522f96e72fd651b40b8128c761111f4c2fd51c26175d46b0e"
)
EXPECTED_REFERENCE_SHA256 = (
    "080536e222f90c5e3d306c8a1b2e81b3276c8355d848e9583e0a6508ab9db7de"
)
MAX_REQUESTS = 8
MAX_SUCCESSFUL_DATA_BODIES = 3
MAX_DECODED_BYTES = 25 * 1024 * 1024
MAX_ACTIVE_SECONDS = 5 * 60

_METADATA_HEADER_ALLOWLIST = (
    "ETag",
    "Content-Type",
    "Content-Encoding",
    "Cache-Control",
    "Vary",
)
_QUOTA_HEADER_ALLOWLIST = (
    "RateLimit-Limit",
    "RateLimit-Remaining",
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
)


class SmokeFailure(RuntimeError):
    """A controlled smoke-test failure whose message contains no secrets."""


def _read_key_file(path: Path) -> str:
    try:
        parent_info = path.parent.lstat()
    except OSError as exc:
        raise SmokeFailure(
            "The designated credential file is unavailable; fill it directly "
            "in an editor before retrying."
        ) from exc
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != os.getuid()
        or stat.S_IMODE(parent_info.st_mode) != 0o700
    ):
        raise SmokeFailure(
            "The designated credential directory must be current-user mode 0700."
        )

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SmokeFailure(
            "The designated credential file is unavailable; fill it directly "
            "in an editor before retrying."
        ) from exc
    try:
        info = os.fstat(descriptor)
        raw = os.read(descriptor, 16 * 1024 + 1)
    except OSError as exc:
        raise SmokeFailure("The designated credential file could not be read.") from exc
    finally:
        os.close(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise SmokeFailure(
            "The designated credential file must be a current-user regular file."
        )
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise SmokeFailure("The designated credential file must have mode 0600.")

    if len(raw) > 16 * 1024 or b"\x00" in raw:
        raise SmokeFailure("The designated credential file has an invalid shape.")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise SmokeFailure("The designated credential file is not UTF-8 text.") from exc
    if len(lines) != 1 or not lines[0].startswith("DATAWISER_API_KEY="):
        raise SmokeFailure(
            "The designated credential file must contain exactly one "
            "DATAWISER_API_KEY= field."
        )
    api_key = lines[0][len("DATAWISER_API_KEY=") :]
    if not api_key:
        raise SmokeFailure(
            "The designated credential file is ready but its API key field is empty."
        )
    return api_key


def _read_api_key() -> str:
    if sys.stdin.isatty():
        api_key = getpass.getpass("Datawiser API key: ")
        if not api_key:
            raise SmokeFailure("A Datawiser API key is required.")
        return api_key
    return _read_key_file(DESIGNATED_KEY_FILE)


def _bounded_header(response: requests.Response, name: str) -> Optional[str]:
    value = response.headers.get(name)
    if value is None:
        return None
    if len(value) > 1024 or any(char in value for char in "\r\n"):
        raise SmokeFailure(f"The response contained an invalid {name} header.")
    return value


@dataclass
class RequestRecord:
    sequence: int
    path: str
    status: int
    validator_present: bool
    response_etag_present: bool
    decoded_size: int
    decoded_sha256: Optional[str]
    request_validator: Optional[str] = field(default=None, repr=False)
    response_etag: Optional[str] = field(default=None, repr=False)
    decoded_body: bytes = field(default=b"", repr=False)
    metadata_headers: dict[str, str] = field(default_factory=dict, repr=False)
    quota_headers: dict[str, str] = field(default_factory=dict)
    operation: str = "unassigned"
    cached_model_returned: bool = False
    security_id: Optional[str] = None
    extra_request: bool = False

    def safe_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "operation": self.operation,
            "path": self.path,
            "status": self.status,
            "validator_present": self.validator_present,
            "response_etag_present": self.response_etag_present,
            "cached_model_returned": self.cached_model_returned,
            "security_id": self.security_id,
            "extra_request": self.extra_request,
            "decoded_size": self.decoded_size,
            "decoded_sha256": self.decoded_sha256,
            "quota_headers": dict(self.quota_headers),
        }


class ResponseObserver:
    """Record a strict, non-secret view of each Requests response."""

    def __init__(self) -> None:
        self.records: list[RequestRecord] = []
        self.decoded_bytes = 0
        self.successful_data_bodies = 0
        expected = urlsplit(APPROVED_PREVIEW_BASE_URL)
        self._expected_origin = (expected.scheme, expected.hostname, expected.port)
        self._expected_prefix = expected.path.rstrip("/") + "/"

    def __call__(
        self, response: requests.Response, *_args: Any, **_kwargs: Any
    ) -> None:
        request = response.request
        parsed = urlsplit(request.url)
        origin = (parsed.scheme, parsed.hostname, parsed.port)
        if (
            request.method != "GET"
            or origin != self._expected_origin
            or not parsed.path.startswith(self._expected_prefix)
            or parsed.query
            or parsed.fragment
        ):
            raise SmokeFailure("A request escaped the approved Preview route.")
        if response.history:
            raise SmokeFailure("A hosted request followed a redirect.")

        is_manifest = parsed.path.startswith(self._expected_prefix + "manifest/")
        body = self._read_bounded_body(response)

        request_etag = request.headers.get("If-None-Match")
        response_etag = _bounded_header(response, "ETag")
        metadata_headers = {
            name: value
            for name in _METADATA_HEADER_ALLOWLIST
            if (value := _bounded_header(response, name)) is not None
        }
        quota_headers = {
            name: value
            for name in _QUOTA_HEADER_ALLOWLIST
            if (value := _bounded_header(response, name)) is not None
        }
        record = RequestRecord(
            sequence=len(self.records) + 1,
            path=parsed.path,
            status=response.status_code,
            validator_present=request_etag is not None,
            response_etag_present=response_etag is not None,
            decoded_size=len(body),
            decoded_sha256=(hashlib.sha256(body).hexdigest() if body else None),
            request_validator=request_etag,
            response_etag=response_etag,
            decoded_body=body,
            metadata_headers=metadata_headers,
            quota_headers=quota_headers,
        )
        self.records.append(record)
        if len(self.records) > MAX_REQUESTS:
            raise SmokeFailure("Hosted request count exceeded the absolute limit.")
        if response.status_code == 200 and not is_manifest:
            self.successful_data_bodies += 1
            if self.successful_data_bodies > MAX_SUCCESSFUL_DATA_BODIES:
                raise SmokeFailure("Successful paid data bodies exceeded the limit.")

    def _read_bounded_body(self, response: requests.Response) -> bytes:
        remaining = MAX_DECODED_BYTES - self.decoded_bytes
        if remaining < 0:
            raise SmokeFailure("Decoded hosted response material exceeded 25 MiB.")

        existing = getattr(response, "_content", False)
        if existing is not False:
            body = bytes(existing)
            if len(body) > remaining:
                raise SmokeFailure("Decoded hosted response material exceeded 25 MiB.")
        else:
            chunks: list[bytes] = []
            received = 0
            try:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    if len(chunk) > remaining - received:
                        response.close()
                        raise SmokeFailure(
                            "Decoded hosted response material exceeded 25 MiB."
                        )
                    chunks.append(chunk)
                    received += len(chunk)
            except requests.RequestException:
                raise
            body = b"".join(chunks)
            response._content = body
            response._content_consumed = True

        self.decoded_bytes += len(body)
        return body


def _tag_new_records(
    observer: ResponseObserver,
    start: int,
    operation: str,
    *,
    cached_model_returned: bool,
    security_id: Optional[str] = None,
) -> list[RequestRecord]:
    records = observer.records[start:]
    for index, record in enumerate(records):
        record.operation = operation
        record.cached_model_returned = cached_model_returned
        record.security_id = security_id
        record.extra_request = index > 0
    return records


def _one_model_call(
    observer: ResponseObserver,
    operation: str,
    expected_path: str,
    expected_status: int,
    call: Callable[[], Any],
) -> tuple[Any, RequestRecord]:
    start = len(observer.records)
    model: Any = None
    try:
        model = call()
    finally:
        records = _tag_new_records(
            observer,
            start,
            operation,
            cached_model_returned=False,
            security_id=getattr(model, "security_id", None),
        )
    if len(records) != 1:
        raise SmokeFailure(f"{operation} made an unexpected extra request.")
    record = records[0]
    if record.path != expected_path or record.status != expected_status:
        raise SmokeFailure(f"{operation} returned an unexpected route or status.")
    record.cached_model_returned = model is not None and record.status == 304
    return model, record


def _assert_diagnostics(client: Client, expected: dict[str, int]) -> None:
    diagnostics = client.diagnostics
    for field_name, expected_value in expected.items():
        if diagnostics.get(field_name) != expected_value:
            raise SmokeFailure(
                f"Client diagnostic {field_name} did not match the bounded sequence."
            )


def _private_write(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _private_json(path: Path, value: Any) -> None:
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _private_write(path, encoded)


def _manifest_field_summary(value: dict[str, Any]) -> dict[str, Any]:
    """Return field-name evidence without returning financial field values."""

    observed: dict[str, dict[str, Any]] = {}
    aapl_key_present = False
    expected_id_fields: set[str] = set()
    aapl_value_fields: set[str] = set()

    def visit(node: Any) -> None:
        nonlocal aapl_key_present
        if isinstance(node, dict):
            for key, child in node.items():
                if not isinstance(key, str):
                    continue
                normalized = key.lower().replace("_", "").replace("-", "")
                if key == "AAPL":
                    aapl_key_present = True
                if child == EXPECTED_SECURITY_ID:
                    expected_id_fields.add(key)
                if child == "AAPL":
                    aapl_value_fields.add(key)
                if any(
                    marker in normalized
                    for marker in ("securityid", "ticker", "country", "qualified")
                ):
                    details = observed.setdefault(
                        key,
                        {"occurrences": 0, "populated": 0, "types": set()},
                    )
                    details["occurrences"] += 1
                    if child is not None and child != "":
                        details["populated"] += 1
                    details["types"].add(type(child).__name__)
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    fields = {
        key: {
            "occurrences": details["occurrences"],
            "populated": details["populated"],
            "types": sorted(details["types"]),
        }
        for key, details in sorted(observed.items())
    }
    return {
        "root_type": "object",
        "root_entry_count": len(value),
        "candidate_fields": fields,
        "expected_security_id_value_fields": sorted(expected_id_fields),
        "aapl_value_fields": sorted(aapl_value_fields),
        "aapl_mapping_key_present": aapl_key_present,
    }


def _capture_manifest(
    client: Client,
    observer: ResponseObserver,
    dataset: str,
    fixture_dir: Path,
) -> tuple[RequestRecord, dict[str, Any], dict[str, str]]:
    start = len(observer.records)
    response = client._transport.get_manifest_response(dataset, updates=False)
    records = _tag_new_records(
        observer,
        start,
        f"metadata:{dataset}",
        cached_model_returned=False,
    )
    if len(records) != 1:
        raise SmokeFailure(f"The {dataset} metadata capture made an extra request.")
    record = records[0]
    expected_path = f"/v1/manifest/{dataset}"
    if (
        record.path != expected_path
        or record.status != 200
        or record.validator_present
        or response.data is None
    ):
        raise SmokeFailure(f"The {dataset} metadata capture was unusable.")

    body_path = fixture_dir / f"{dataset}.decoded.json"
    headers_path = fixture_dir / f"{dataset}.headers.json"
    _private_write(body_path, record.decoded_body)
    _private_json(headers_path, record.metadata_headers)
    paths = {"body": str(body_path), "headers": str(headers_path)}
    return record, _manifest_field_summary(response.data), paths


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, SmokeFailure):
        return str(exc)
    if isinstance(exc, DatawiserAPIError):
        return f"{type(exc).__name__} (HTTP {exc.status_code})"
    return type(exc).__name__


def _deadline_expired(_signum: int, _frame: Any) -> None:
    raise SmokeFailure("Active hosted execution exceeded five minutes.")


def _execute(client: Client, observer: ResponseObserver, run_dir: Path) -> dict[str, Any]:
    base_path = urlsplit(APPROVED_PREVIEW_BASE_URL).path.rstrip("/")
    so_path = base_path + "/shares-outstanding/AAPL"
    ref_path = base_path + "/reference/AAPL"
    denied_path = base_path + "/reference/AAPL-CA"
    started = time.monotonic()

    so_first, so_first_record = _one_model_call(
        observer,
        "shares_outstanding:AAPL:first",
        so_path,
        200,
        lambda: client.shares_outstanding("AAPL"),
    )
    if (
        so_first.security_id != EXPECTED_SECURITY_ID
        or len(so_first) != EXPECTED_SO_EVENTS
        or so_first.latest() is None
        or len(tuple(iter(so_first))) != EXPECTED_SO_EVENTS
        or not so_first_record.response_etag_present
        or so_first_record.decoded_sha256 != EXPECTED_SO_SHA256
    ):
        raise SmokeFailure("The first AAPL shares-outstanding response changed.")

    so_second, so_second_record = _one_model_call(
        observer,
        "shares_outstanding:AAPL:second",
        so_path,
        304,
        lambda: client.shares_outstanding("AAPL"),
    )
    if (
        so_second != so_first
        or not so_second_record.validator_present
        or so_second_record.request_validator != so_first_record.response_etag
    ):
        raise SmokeFailure("The AAPL shares-outstanding cache revalidation failed.")
    _assert_diagnostics(
        client,
        {
            "data_requests": 2,
            "conditional_requests": 1,
            "not_modified_responses": 1,
            "cached_304_returns": 1,
            "unconditional_recoveries": 0,
            "responses_without_etag": 0,
            "manifest_requests": 0,
            "cache_write_failures": 0,
        },
    )

    ref_first, ref_first_record = _one_model_call(
        observer,
        "reference:AAPL:first",
        ref_path,
        200,
        lambda: client.reference("AAPL"),
    )
    if (
        ref_first.security_id != EXPECTED_SECURITY_ID
        or not isinstance(ref_first.raw, dict)
        or not ref_first.raw
        or not ref_first_record.response_etag_present
        or ref_first_record.decoded_sha256 != EXPECTED_REFERENCE_SHA256
    ):
        raise SmokeFailure("The first AAPL reference response changed.")

    ref_second, ref_second_record = _one_model_call(
        observer,
        "reference:AAPL:second",
        ref_path,
        304,
        lambda: client.reference("AAPL"),
    )
    if (
        ref_second != ref_first
        or ref_second.raw != ref_first.raw
        or not ref_second_record.validator_present
        or ref_second_record.request_validator != ref_first_record.response_etag
    ):
        raise SmokeFailure("The AAPL reference cache revalidation failed.")

    denial_start = len(observer.records)
    denial_status: Optional[int] = None
    denied_model: Any = None
    try:
        denied_model = client.reference("AAPL-CA")
    except (DatawiserAccessError, DatawiserNotFoundError) as exc:
        denial_status = exc.status_code
        if denial_status not in {403, 404}:
            raise
    finally:
        denial_records = _tag_new_records(
            observer,
            denial_start,
            "reference:AAPL-CA:denial",
            cached_model_returned=False,
            security_id=getattr(denied_model, "security_id", None),
        )
    if denied_model is not None or denial_status not in {403, 404}:
        raise SmokeFailure("AAPL-CA unexpectedly returned an authorised body.")
    if (
        len(denial_records) != 1
        or denial_records[0].path != denied_path
        or denial_records[0].status != denial_status
        or denial_records[0].validator_present
    ):
        raise SmokeFailure("The AAPL-CA denial request was not isolated.")

    _assert_diagnostics(
        client,
        {
            "data_requests": 5,
            "conditional_requests": 2,
            "not_modified_responses": 2,
            "cached_304_returns": 2,
            "unconditional_recoveries": 0,
            "responses_without_etag": 0,
            "manifest_requests": 0,
            "cache_write_failures": 0,
        },
    )
    if any("/manifest/" in record.path for record in observer.records):
        raise SmokeFailure("An item call unexpectedly fetched a manifest.")

    fixture_dir = run_dir / "manifest-fixtures"
    fixture_dir.mkdir(mode=0o700)
    _, so_manifest, so_paths = _capture_manifest(
        client, observer, "shares-outstanding", fixture_dir
    )
    _, ref_manifest, ref_paths = _capture_manifest(
        client, observer, "reference", fixture_dir
    )

    elapsed = time.monotonic() - started
    if elapsed > MAX_ACTIVE_SECONDS:
        raise SmokeFailure("Active hosted execution exceeded five minutes.")
    if len(observer.records) != 7:
        raise SmokeFailure("The successful sequence did not use exactly seven requests.")

    return {
        "status": "passed",
        "base_url": APPROVED_PREVIEW_BASE_URL,
        "hosted_execution_occurred": True,
        "python_version": platform.python_version(),
        "requests_version": requests.__version__,
        "installed_version": datawiserai.__version__,
        "installed_module": str(Path(datawiserai.__file__).resolve()),
        "request_rows": [record.safe_dict() for record in observer.records],
        "diagnostics": client.diagnostics,
        "counts": {
            "total_requests": len(observer.records),
            "data_200_bodies": observer.successful_data_bodies,
            "not_modified_304": sum(
                record.status == 304 for record in observer.records
            ),
            "metadata_calls": sum(
                record.operation.startswith("metadata:")
                for record in observer.records
            ),
            "denials": sum(
                record.status in {403, 404} for record in observer.records
            ),
            "recovery_calls": client.diagnostics["unconditional_recoveries"],
            "decoded_bytes": observer.decoded_bytes,
        },
        "body_comparison": {
            "shares_outstanding_equal_after_304": so_first == so_second,
            "reference_equal_after_304": ref_first == ref_second,
            "shares_outstanding_decoded_sha256": so_first_record.decoded_sha256,
            "reference_decoded_sha256": ref_first_record.decoded_sha256,
            "shares_outstanding_events": len(so_first),
            "security_id": EXPECTED_SECURITY_ID,
            "aapl_ca_status": denial_status,
        },
        "manifest_fields": {
            "shares-outstanding": so_manifest,
            "reference": ref_manifest,
        },
        "private_fixture_paths": {
            "shares-outstanding": so_paths,
            "reference": ref_paths,
        },
        "persisted_quota_delta": "not independently measured",
        "active_seconds": elapsed,
    }


def main() -> None:
    module_path = Path(datawiserai.__file__).resolve()
    if not any(part in {"site-packages", "dist-packages"} for part in module_path.parts):
        print("D4-H preflight failed: datawiserai was not imported from site-packages.")
        raise SystemExit(1)
    if datawiserai.__version__ != "1.1.0":
        print("D4-H preflight failed: the installed client is not version 1.1.0.")
        raise SystemExit(1)

    try:
        api_key = _read_api_key()
    except Exception as exc:
        print(f"D4-H preflight failed: {_safe_error(exc)}")
        raise SystemExit(1) from None

    run_dir = Path(tempfile.mkdtemp(prefix="datawiserai-d4h-"))
    run_dir.chmod(0o700)
    observer = ResponseObserver()
    summary: dict[str, Any]
    client: Optional[Client] = None
    try:
        with Client(
            api_key,
            cache_dir=run_dir / "cache",
            base_url=APPROVED_PREVIEW_BASE_URL,
            timeout=30,
        ) as client:
            api_key = ""
            session = getattr(getattr(client, "_transport", None), "_session", None)
            if not isinstance(session, requests.Session):
                raise SmokeFailure("The installed transport observation seam is absent.")
            if client.base_url != APPROVED_PREVIEW_BASE_URL:
                raise SmokeFailure("The client did not retain the approved Preview base.")
            session.hooks.setdefault("response", []).append(observer)
            previous_alarm_handler = signal.signal(signal.SIGALRM, _deadline_expired)
            signal.setitimer(signal.ITIMER_REAL, MAX_ACTIVE_SECONDS)
            try:
                summary = _execute(client, observer, run_dir)
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, previous_alarm_handler)
    except Exception as exc:
        api_key = ""
        summary = {
            "status": "failed",
            "base_url": APPROVED_PREVIEW_BASE_URL,
            "hosted_execution_occurred": bool(observer.records),
            "safe_error": _safe_error(exc),
            "request_rows": [record.safe_dict() for record in observer.records],
            "persisted_quota_delta": "not independently measured",
        }
        try:
            _private_json(run_dir / "result.json", summary)
            print(f"Private safe result: {run_dir / 'result.json'}")
        except Exception:
            print("Private safe result could not be written.")
        print(f"D4-H hosted smoke FAILED: {_safe_error(exc)}")
        raise SystemExit(1) from None

    result_path = run_dir / "result.json"
    _private_json(result_path, summary)
    print("D4-H hosted smoke PASSED")
    print(f"Installed version: {summary['installed_version']}")
    print(f"Installed module: {summary['installed_module']}")
    for row in summary["request_rows"]:
        print(
            "Request {sequence}: {operation} {path} -> {status}; "
            "validator={validator_present}; cached={cached_model_returned}; "
            "extra={extra_request}".format(**row)
        )
    print(f"Client diagnostics: {summary['diagnostics']}")
    print(f"Private safe result: {result_path}")
    print(f"Private manifest fixtures: {run_dir / 'manifest-fixtures'}")
    print("Persisted quota delta: not independently measured")


if __name__ == "__main__":
    main()
