from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

from ._transport import (
    canonicalize_base_url as _canonical_api_base,
    is_valid_etag as _is_valid_etag,
)

try:  # pragma: no cover - exercised on POSIX, optional elsewhere
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]


CACHE_SCHEMA_VERSION = 2
DEFAULT_MODEL_VERSION = "1"
DEFAULT_MAX_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_ENTRIES = 2_048

_CACHE_ROOT_NAME = "v2"
_MAX_TEXT_BYTES = 16 * 1024
_MAX_DECOMPRESSED_BYTES = 256 * 1024 * 1024
_MAX_FUTURE_CLOCK_SKEW_SECONDS = 5 * 60
_JSON_SUFFIX = ".json.gz"
_HEX_256_RE = re.compile(r"[0-9a-f]{64}\Z")

_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}


@dataclass(frozen=True)
class CacheCandidate:
    """An intact representation associated with one exact request alias.

    ``etag`` is either an unmodified, syntactically valid server entity-tag or
    ``None``.  The private locator fields bind a candidate to the immutable
    body that was checked before a conditional request.  They allow
    :meth:`FileCache.reload_data` to load that exact body after a 304 even if a
    concurrent request has since replaced the alias.
    """

    dataset: str
    original_identifier: str
    security_id: str
    data: dict[str, Any]
    etag: Optional[str]
    source_last_update: Optional[str]
    cached_at: float
    body_sha256: str
    _body_path: Path = field(repr=False, compare=False)
    _api_base_sha256: str = field(repr=False, compare=False)
    _credential_sha256: str = field(repr=False, compare=False)
    _model_sha256: str = field(repr=False, compare=False)
    _dataset_sha256: str = field(repr=False, compare=False)
    _security_id_sha256: str = field(repr=False, compare=False)
    _etag_sha256: str = field(repr=False, compare=False)

    @property
    def body_path(self) -> Path:
        """Hashed path to the candidate's immutable local body."""

        return self._body_path


class FileCache:
    """Versioned, isolated, integrity-checked on-disk response cache.

    Version 2 data lives below a namespace derived from the complete
    canonical API base, the credential, and the local model version.  Values
    used in paths are domain-separated SHA-256 digests; in particular, the
    API key itself is never written to disk.  The older endpoint/ticker files
    directly below ``cache_dir`` are deliberately neither read nor removed.
    """

    def __init__(
        self,
        cache_dir: Path,
        *,
        api_base: str,
        api_key: str,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        model_version: str = DEFAULT_MODEL_VERSION,
    ) -> None:
        self._dir = Path(cache_dir)
        self._canonical_api_base = _canonical_api_base(api_base)
        api_key = _bounded_text(api_key, "api_key", allow_whitespace=True)
        if not api_key:
            raise ValueError("api_key must not be empty")

        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
            raise TypeError("max_bytes must be an integer")
        if isinstance(max_entries, bool) or not isinstance(max_entries, int):
            raise TypeError("max_entries must be an integer")
        if max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        if max_entries < 0:
            raise ValueError("max_entries must be non-negative")

        self._max_bytes = max_bytes
        self._max_entries = max_entries
        self._model_version = _bounded_text(
            str(model_version), "model_version", allow_whitespace=True
        )
        if not self._model_version:
            raise ValueError("model_version must not be empty")

        self._api_base_sha256 = _component_digest(
            "api-base", self._canonical_api_base
        )
        self._credential_sha256 = _component_digest("credential", api_key)
        self._model_sha256 = _component_digest(
            "model-version", self._model_version
        )

        # Drop the only reference held here as soon as its one-way namespace
        # digest has been derived.
        del api_key

        self._namespace_dir = (
            self._dir
            / _CACHE_ROOT_NAME
            / self._api_base_sha256
            / self._credential_sha256
            / self._model_sha256
        )
        self._ensure_initial_directories()

        lock_key = os.path.abspath(os.fspath(self._namespace_dir))
        with _LOCKS_GUARD:
            self._thread_lock = _LOCKS.setdefault(lock_key, threading.RLock())

    @property
    def namespace_dir(self) -> Path:
        """The current hashed v2 namespace, for diagnostics and tests."""

        return self._namespace_dir

    @property
    def namespace_path(self) -> Path:
        """Backward-friendly alias for :attr:`namespace_dir`."""

        return self._namespace_dir

    def get_data(
        self, dataset: str, original_identifier: str
    ) -> Optional[CacheCandidate]:
        """Return an intact candidate for an exact request alias, if present."""

        dataset = _bounded_text(dataset, "dataset")
        original_identifier = _bounded_text(
            original_identifier, "original_identifier", allow_whitespace=True
        )
        dataset_sha256 = _component_digest("dataset", dataset)
        identifier_sha256 = _component_digest(
            "request-identifier", original_identifier
        )
        alias_path = self._alias_path(dataset_sha256, identifier_sha256)

        with self._namespace_lock():
            alias = self._read_envelope(alias_path)
            if alias is None:
                self._discard_file(alias_path)
                return None
            parsed = self._validate_alias(
                alias,
                alias_path,
                dataset_sha256=dataset_sha256,
                identifier_sha256=identifier_sha256,
            )
            if parsed is None:
                self._discard_file(alias_path)
                return None

            body_path = self._body_path(
                dataset_sha256,
                parsed["security_id_sha256"],
                parsed["etag_sha256"],
                parsed["body_sha256"],
            )
            data = self._load_valid_body(
                body_path,
                dataset_sha256=dataset_sha256,
                security_id=parsed["security_id"],
                security_id_sha256=parsed["security_id_sha256"],
                etag=parsed["etag"],
                etag_sha256=parsed["etag_sha256"],
                body_sha256=parsed["body_sha256"],
            )
            if data is None:
                # The alias is unusable without its exact body.  Removing only
                # this pointer cannot affect another representation or v1.
                self._discard_file(alias_path)
                return None

            return CacheCandidate(
                dataset=dataset,
                original_identifier=original_identifier,
                security_id=parsed["security_id"],
                data=data,
                etag=parsed["etag"],
                source_last_update=parsed["source_last_update"],
                cached_at=parsed["cached_at"],
                body_sha256=parsed["body_sha256"],
                _body_path=body_path,
                _api_base_sha256=self._api_base_sha256,
                _credential_sha256=self._credential_sha256,
                _model_sha256=self._model_sha256,
                _dataset_sha256=dataset_sha256,
                _security_id_sha256=parsed["security_id_sha256"],
                _etag_sha256=parsed["etag_sha256"],
            )

    def reload_data(
        self, candidate: CacheCandidate
    ) -> Optional[dict[str, Any]]:
        """Reload and validate the exact body represented by *candidate*.

        This method intentionally does not follow the mutable alias record.
        It is the post-304 check: a missing or corrupt body returns ``None`` so
        the caller can perform its single allowed unconditional recovery GET.
        """

        if not isinstance(candidate, CacheCandidate):
            return None
        if (
            candidate._api_base_sha256 != self._api_base_sha256
            or candidate._credential_sha256 != self._credential_sha256
            or candidate._model_sha256 != self._model_sha256
        ):
            return None

        expected_dataset_sha256 = _component_digest(
            "dataset", candidate.dataset
        )
        expected_security_id_sha256 = _component_digest(
            "security-id", candidate.security_id
        )
        expected_etag_sha256 = _etag_digest(candidate.etag)
        if (
            expected_dataset_sha256 != candidate._dataset_sha256
            or expected_security_id_sha256 != candidate._security_id_sha256
            or expected_etag_sha256 != candidate._etag_sha256
            or not _is_sha256(candidate.body_sha256)
        ):
            return None

        expected_path = self._body_path(
            candidate._dataset_sha256,
            candidate._security_id_sha256,
            candidate._etag_sha256,
            candidate.body_sha256,
        )
        if candidate._body_path != expected_path:
            return None

        with self._namespace_lock():
            return self._load_valid_body(
                expected_path,
                dataset_sha256=candidate._dataset_sha256,
                security_id=candidate.security_id,
                security_id_sha256=candidate._security_id_sha256,
                etag=candidate.etag,
                etag_sha256=candidate._etag_sha256,
                body_sha256=candidate.body_sha256,
            )

    # A descriptive spelling for callers that want to make the integrity
    # check explicit.  It has the same exact-body semantics as reload_data.
    validate_candidate = reload_data

    def put_data(
        self,
        dataset: str,
        original_identifier: str,
        security_id: str,
        data: dict[str, Any],
        etag: Optional[str] = None,
        source_last_update: Optional[str] = None,
    ) -> CacheCandidate:
        """Atomically publish a validated body, then its exact request alias.

        Invalid response ETags are retained as no validator.  The data remains
        cacheable, but a later request must be unconditional.
        """

        dataset = _bounded_text(dataset, "dataset")
        original_identifier = _bounded_text(
            original_identifier, "original_identifier", allow_whitespace=True
        )
        security_id = _bounded_text(
            security_id, "security_id", allow_whitespace=True
        )
        if not security_id.strip():
            raise ValueError("security_id must not be empty")
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary")
        if _payload_security_id(data) != security_id:
            raise ValueError("security_id does not match the response payload")
        if source_last_update is not None:
            source_last_update = _bounded_text(
                source_last_update,
                "source_last_update",
                allow_whitespace=True,
            )

        etag = etag if _is_valid_etag(etag) else None
        dataset_sha256 = _component_digest("dataset", dataset)
        identifier_sha256 = _component_digest(
            "request-identifier", original_identifier
        )
        security_id_sha256 = _component_digest("security-id", security_id)
        etag_sha256 = _etag_digest(etag)
        body_bytes = _canonical_json_bytes(data)
        body_sha256 = hashlib.sha256(body_bytes).hexdigest()
        cached_at = time.time()

        body_path = self._body_path(
            dataset_sha256,
            security_id_sha256,
            etag_sha256,
            body_sha256,
        )
        alias_path = self._alias_path(dataset_sha256, identifier_sha256)
        body_envelope = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "model_version": self._model_version,
            "kind": "data-body",
            "api_base_sha256": self._api_base_sha256,
            "credential_sha256": self._credential_sha256,
            "model_sha256": self._model_sha256,
            "dataset_sha256": dataset_sha256,
            "security_id": security_id,
            "security_id_sha256": security_id_sha256,
            "etag": etag,
            "etag_sha256": etag_sha256,
            "body_sha256": body_sha256,
            "created_at": cached_at,
            "data": data,
        }
        alias_envelope = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "model_version": self._model_version,
            "kind": "data-alias",
            "api_base_sha256": self._api_base_sha256,
            "credential_sha256": self._credential_sha256,
            "model_sha256": self._model_sha256,
            "dataset_sha256": dataset_sha256,
            "identifier_sha256": identifier_sha256,
            "security_id": security_id,
            "security_id_sha256": security_id_sha256,
            "etag": etag,
            "etag_sha256": etag_sha256,
            "body_sha256": body_sha256,
            "source_last_update": source_last_update,
            "cached_at": cached_at,
        }

        with self._namespace_lock():
            existing = self._read_envelope(body_path)
            if existing is None or not self._valid_body_envelope(
                existing,
                dataset_sha256=dataset_sha256,
                security_id=security_id,
                security_id_sha256=security_id_sha256,
                etag=etag,
                etag_sha256=etag_sha256,
                body_sha256=body_sha256,
            ):
                if existing is not None:
                    self._discard_file(body_path)
                self._atomic_write_envelope(body_path, body_envelope)

            # The immutable body is fully durable before this mutable pointer
            # becomes visible.
            self._atomic_write_envelope(alias_path, alias_envelope)
            self._prune()

            # A zero/tiny bound can immediately evict the just-written entry.
            # Returning the in-memory response is still useful to the caller;
            # reload_data will correctly report that it is no longer cached.
            return CacheCandidate(
                dataset=dataset,
                original_identifier=original_identifier,
                security_id=security_id,
                data=json.loads(body_bytes.decode("utf-8")),
                etag=etag,
                source_last_update=source_last_update,
                cached_at=cached_at,
                body_sha256=body_sha256,
                _body_path=body_path,
                _api_base_sha256=self._api_base_sha256,
                _credential_sha256=self._credential_sha256,
                _model_sha256=self._model_sha256,
                _dataset_sha256=dataset_sha256,
                _security_id_sha256=security_id_sha256,
                _etag_sha256=etag_sha256,
            )

    def get_manifest(
        self, dataset: str, max_age_seconds: Optional[float] = None
    ) -> Optional[dict[str, Any]]:
        """Return cached manifest data if intact and within *max_age_seconds*."""

        dataset = _bounded_text(dataset, "dataset")
        if max_age_seconds is not None:
            if isinstance(max_age_seconds, bool) or not isinstance(
                max_age_seconds, (int, float)
            ):
                raise TypeError("max_age_seconds must be a number or None")
            if not math.isfinite(float(max_age_seconds)) or max_age_seconds < 0:
                raise ValueError("max_age_seconds must be finite and non-negative")

        dataset_sha256 = _component_digest("dataset", dataset)
        path = self._manifest_path(dataset_sha256)
        with self._namespace_lock():
            envelope = self._read_envelope(path)
            if envelope is None:
                self._discard_file(path)
                return None
            data = self._validate_manifest(
                envelope, path, dataset_sha256=dataset_sha256
            )
            if data is None:
                self._discard_file(path)
                return None
            fetched_at = float(envelope["fetched_at"])
            now = time.time()
            if fetched_at > now + _MAX_FUTURE_CLOCK_SKEW_SECONDS:
                self._discard_file(path)
                return None
            if (
                max_age_seconds is not None
                and max(0.0, now - fetched_at) > max_age_seconds
            ):
                return None
            return data

    def put_manifest(
        self,
        dataset: str,
        data: dict[str, Any],
        fetched_at: Optional[float] = None,
        *,
        etag: Optional[str] = None,
    ) -> None:
        """Atomically cache metadata with the time it was fetched."""

        dataset = _bounded_text(dataset, "dataset")
        if not isinstance(data, dict):
            raise TypeError("manifest data must be a dictionary")
        if fetched_at is None:
            fetched_at = time.time()
        if isinstance(fetched_at, bool) or not isinstance(fetched_at, (int, float)):
            raise TypeError("fetched_at must be a number")
        fetched_at = float(fetched_at)
        if not math.isfinite(fetched_at) or fetched_at < 0:
            raise ValueError("fetched_at must be finite and non-negative")
        etag = etag if _is_valid_etag(etag) else None

        dataset_sha256 = _component_digest("dataset", dataset)
        etag_sha256 = _etag_digest(etag)
        body_sha256 = hashlib.sha256(_canonical_json_bytes(data)).hexdigest()
        envelope = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "model_version": self._model_version,
            "kind": "manifest",
            "api_base_sha256": self._api_base_sha256,
            "credential_sha256": self._credential_sha256,
            "model_sha256": self._model_sha256,
            "dataset_sha256": dataset_sha256,
            "etag": etag,
            "etag_sha256": etag_sha256,
            "body_sha256": body_sha256,
            "fetched_at": fetched_at,
            "data": data,
        }
        path = self._manifest_path(dataset_sha256)
        with self._namespace_lock():
            self._atomic_write_envelope(path, envelope)
            self._prune()

    def clear(self, dataset: Optional[str] = None) -> int:
        """Remove v2 cache files in this namespace and return a file count.

        Legacy timestamp-only files and other base/key/model namespaces are
        outside the traversal root and remain untouched.
        """

        with self._namespace_lock():
            if dataset is None:
                roots = [
                    self._namespace_dir / "aliases",
                    self._namespace_dir / "data",
                    self._namespace_dir / "manifests",
                ]
            else:
                dataset = _bounded_text(dataset, "dataset")
                dataset_sha256 = _component_digest("dataset", dataset)
                roots = [
                    self._namespace_dir / "aliases" / dataset_sha256,
                    self._namespace_dir / "data" / dataset_sha256,
                    self._namespace_dir / "manifests" / dataset_sha256,
                ]

            removed = sum(self._clear_cache_files(root) for root in roots)
            for root in roots:
                self._remove_empty_dirs(root)
            self._remove_empty_dirs(self._namespace_dir / "aliases")
            self._remove_empty_dirs(self._namespace_dir / "data")
            self._remove_empty_dirs(self._namespace_dir / "manifests")
            return removed

    # ------------------------------------------------------------------
    # Paths and validation
    # ------------------------------------------------------------------

    def _alias_path(self, dataset_sha256: str, identifier_sha256: str) -> Path:
        return (
            self._namespace_dir
            / "aliases"
            / dataset_sha256
            / f"{identifier_sha256}{_JSON_SUFFIX}"
        )

    def _body_path(
        self,
        dataset_sha256: str,
        security_id_sha256: str,
        etag_sha256: str,
        body_sha256: str,
    ) -> Path:
        return (
            self._namespace_dir
            / "data"
            / dataset_sha256
            / security_id_sha256
            / etag_sha256
            / f"{body_sha256}{_JSON_SUFFIX}"
        )

    def _manifest_path(self, dataset_sha256: str) -> Path:
        slot = _component_digest("manifest-slot", "current")
        return (
            self._namespace_dir
            / "manifests"
            / dataset_sha256
            / f"{slot}{_JSON_SUFFIX}"
        )

    def _validate_alias(
        self,
        envelope: dict[str, Any],
        path: Path,
        *,
        dataset_sha256: str,
        identifier_sha256: str,
    ) -> Optional[dict[str, Any]]:
        if not self._valid_common(
            envelope, kind="data-alias", dataset_sha256=dataset_sha256
        ):
            return None
        if envelope.get("identifier_sha256") != identifier_sha256:
            return None
        if path != self._alias_path(dataset_sha256, identifier_sha256):
            return None

        security_id = envelope.get("security_id")
        if not isinstance(security_id, str) or not security_id.strip():
            return None
        security_id_sha256 = envelope.get("security_id_sha256")
        if security_id_sha256 != _component_digest("security-id", security_id):
            return None

        etag = envelope.get("etag")
        if etag is not None and not _is_valid_etag(etag):
            return None
        etag_sha256 = envelope.get("etag_sha256")
        if etag_sha256 != _etag_digest(etag):
            return None
        body_sha256 = envelope.get("body_sha256")
        if not _is_sha256(body_sha256):
            return None
        source_last_update = envelope.get("source_last_update")
        if source_last_update is not None and not isinstance(
            source_last_update, str
        ):
            return None
        cached_at = envelope.get("cached_at")
        if not _valid_timestamp(cached_at):
            return None

        return {
            "security_id": security_id,
            "security_id_sha256": security_id_sha256,
            "etag": etag,
            "etag_sha256": etag_sha256,
            "body_sha256": body_sha256,
            "source_last_update": source_last_update,
            "cached_at": float(cached_at),
        }

    def _load_valid_body(
        self,
        path: Path,
        *,
        dataset_sha256: str,
        security_id: str,
        security_id_sha256: str,
        etag: Optional[str],
        etag_sha256: str,
        body_sha256: str,
    ) -> Optional[dict[str, Any]]:
        envelope = self._read_envelope(path)
        if envelope is None:
            self._discard_file(path)
            return None
        if not self._valid_body_envelope(
            envelope,
            dataset_sha256=dataset_sha256,
            security_id=security_id,
            security_id_sha256=security_id_sha256,
            etag=etag,
            etag_sha256=etag_sha256,
            body_sha256=body_sha256,
        ):
            self._discard_file(path)
            return None
        data = envelope.get("data")
        assert isinstance(data, dict)  # established above
        return data

    def _valid_body_envelope(
        self,
        envelope: dict[str, Any],
        *,
        dataset_sha256: str,
        security_id: str,
        security_id_sha256: str,
        etag: Optional[str],
        etag_sha256: str,
        body_sha256: str,
    ) -> bool:
        if not self._valid_common(
            envelope, kind="data-body", dataset_sha256=dataset_sha256
        ):
            return False
        if (
            envelope.get("security_id") != security_id
            or envelope.get("security_id_sha256") != security_id_sha256
            or security_id_sha256
            != _component_digest("security-id", security_id)
            or envelope.get("etag") != etag
            or envelope.get("etag_sha256") != etag_sha256
            or etag_sha256 != _etag_digest(etag)
            or envelope.get("body_sha256") != body_sha256
            or not _is_sha256(body_sha256)
        ):
            return False
        if etag is not None and not _is_valid_etag(etag):
            return False
        if not _valid_timestamp(envelope.get("created_at")):
            return False
        data = envelope.get("data")
        if not isinstance(data, dict) or _payload_security_id(data) != security_id:
            return False
        try:
            actual_sha256 = hashlib.sha256(_canonical_json_bytes(data)).hexdigest()
        except (TypeError, ValueError, UnicodeError):
            return False
        return actual_sha256 == body_sha256

    def _validate_manifest(
        self,
        envelope: dict[str, Any],
        path: Path,
        *,
        dataset_sha256: str,
    ) -> Optional[dict[str, Any]]:
        if not self._valid_common(
            envelope, kind="manifest", dataset_sha256=dataset_sha256
        ):
            return None
        if path != self._manifest_path(dataset_sha256):
            return None
        etag = envelope.get("etag")
        if etag is not None and not _is_valid_etag(etag):
            return None
        if envelope.get("etag_sha256") != _etag_digest(etag):
            return None
        if not _valid_timestamp(envelope.get("fetched_at")):
            return None
        body_sha256 = envelope.get("body_sha256")
        data = envelope.get("data")
        if not _is_sha256(body_sha256) or not isinstance(data, dict):
            return None
        try:
            actual_sha256 = hashlib.sha256(_canonical_json_bytes(data)).hexdigest()
        except (TypeError, ValueError, UnicodeError):
            return None
        return data if actual_sha256 == body_sha256 else None

    def _valid_common(
        self,
        envelope: dict[str, Any],
        *,
        kind: str,
        dataset_sha256: str,
    ) -> bool:
        return (
            envelope.get("schema_version") == CACHE_SCHEMA_VERSION
            and envelope.get("model_version") == self._model_version
            and envelope.get("model_sha256") == self._model_sha256
            and envelope.get("kind") == kind
            and envelope.get("api_base_sha256") == self._api_base_sha256
            and envelope.get("credential_sha256") == self._credential_sha256
            and envelope.get("dataset_sha256") == dataset_sha256
            and _is_sha256(dataset_sha256)
        )

    # ------------------------------------------------------------------
    # Safe I/O
    # ------------------------------------------------------------------

    def _ensure_initial_directories(self) -> None:
        self._mkdir_checked(self._dir, chmod_if_existing=False)
        current = self._dir
        for part in self._namespace_dir.relative_to(self._dir).parts:
            current = current / part
            self._mkdir_checked(current, chmod_if_existing=True)

    def _mkdir_checked(self, path: Path, *, chmod_if_existing: bool = True) -> None:
        created = False
        try:
            info = path.lstat()
        except FileNotFoundError:
            path.mkdir(mode=0o700, parents=path == self._dir)
            created = True
            info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise OSError(f"cache path is not a regular directory: {path}")
        if created or chmod_if_existing:
            try:
                os.chmod(path, 0o700, follow_symlinks=False)
            except (NotImplementedError, OSError):
                pass

    def _ensure_parent(self, path: Path) -> None:
        try:
            relative = path.parent.relative_to(self._namespace_dir)
        except ValueError as exc:
            raise OSError("cache path escapes its namespace") from exc
        current = self._namespace_dir
        self._mkdir_checked(current, chmod_if_existing=True)
        for part in relative.parts:
            current = current / part
            self._mkdir_checked(current, chmod_if_existing=True)

    def _safe_regular_file(self, path: Path) -> bool:
        try:
            relative = path.relative_to(self._namespace_dir)
        except ValueError:
            return False
        current = self._namespace_dir
        try:
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                return False
            for part in relative.parts[:-1]:
                current = current / part
                info = current.lstat()
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                    return False
            info = path.lstat()
        except (FileNotFoundError, OSError):
            return False
        return stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)

    def _read_envelope(self, path: Path) -> Optional[dict[str, Any]]:
        if not self._safe_regular_file(path):
            return None
        try:
            compressed_size = path.lstat().st_size
            if compressed_size > max(self._max_bytes, 1024 * 1024 * 1024):
                return None
            with gzip.open(path, "rb") as handle:
                raw = handle.read(_MAX_DECOMPRESSED_BYTES + 1)
            if len(raw) > _MAX_DECOMPRESSED_BYTES:
                return None
            decoded = json.loads(raw.decode("utf-8"))
            return decoded if isinstance(decoded, dict) else None
        except (
            EOFError,
            gzip.BadGzipFile,
            json.JSONDecodeError,
            OSError,
            UnicodeDecodeError,
            ValueError,
        ):
            return None

    def _atomic_write_envelope(
        self, path: Path, envelope: dict[str, Any]
    ) -> None:
        self._ensure_parent(path)
        try:
            destination_info = path.lstat()
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(destination_info.st_mode):
                raise OSError(f"refusing to replace non-regular cache file: {path}")

        payload = gzip.compress(_canonical_json_bytes(envelope), mtime=0)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=".tmp-", suffix=_JSON_SUFFIX, dir=os.fspath(path.parent)
        )
        temporary_path = Path(temporary_name)
        try:
            try:
                os.fchmod(file_descriptor, 0o600)
            except (AttributeError, OSError):
                pass
            with os.fdopen(file_descriptor, "wb") as handle:
                file_descriptor = -1
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
            try:
                os.chmod(path, 0o600, follow_symlinks=False)
            except (NotImplementedError, OSError):
                pass
            self._fsync_directory(path.parent)
        finally:
            if file_descriptor >= 0:
                os.close(file_descriptor)
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        try:
            descriptor = os.open(os.fspath(path), flags)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    @contextmanager
    def _namespace_lock(self) -> Iterator[None]:
        with self._thread_lock:
            self._mkdir_checked(self._namespace_dir, chmod_if_existing=True)
            descriptor = os.open(os.fspath(self._namespace_dir), os.O_RDONLY)
            try:
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def _discard_file(self, path: Path) -> bool:
        try:
            info = path.lstat()
        except (FileNotFoundError, OSError):
            return False
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
            return False
        try:
            path.unlink()
            return True
        except OSError:
            return False

    # ------------------------------------------------------------------
    # Deterministic namespace-local pruning and clearing
    # ------------------------------------------------------------------

    def _prune(self) -> None:
        items, referenced_bodies = self._retention_items()
        self._remove_orphan_bodies(referenced_bodies)
        items.sort(key=lambda item: (item[0], item[1].as_posix()))

        while len(items) > self._max_entries:
            _timestamp, path, _body = items.pop(0)
            self._discard_file(path)

        referenced_bodies = {body for _ts, _path, body in items if body is not None}
        self._remove_orphan_bodies(referenced_bodies)

        while items and self._namespace_size() > self._max_bytes:
            _timestamp, path, _body = items.pop(0)
            self._discard_file(path)
            referenced_bodies = {
                body for _ts, _path, body in items if body is not None
            }
            self._remove_orphan_bodies(referenced_bodies)

    def _retention_items(
        self,
    ) -> tuple[list[tuple[float, Path, Optional[Path]]], set[Path]]:
        items: list[tuple[float, Path, Optional[Path]]] = []
        referenced_bodies: set[Path] = set()

        aliases_root = self._namespace_dir / "aliases"
        for path in self._walk_cache_files(aliases_root):
            envelope = self._read_envelope(path)
            parsed = self._validate_alias_for_pruning(envelope, path)
            if parsed is None:
                self._discard_file(path)
                continue
            body_path = self._body_path(
                parsed["dataset_sha256"],
                parsed["security_id_sha256"],
                parsed["etag_sha256"],
                parsed["body_sha256"],
            )
            items.append((parsed["cached_at"], path, body_path))
            referenced_bodies.add(body_path)

        manifests_root = self._namespace_dir / "manifests"
        for path in self._walk_cache_files(manifests_root):
            envelope = self._read_envelope(path)
            parsed = self._validate_manifest_for_pruning(envelope, path)
            if parsed is None:
                self._discard_file(path)
                continue
            items.append((parsed, path, None))

        return items, referenced_bodies

    def _validate_alias_for_pruning(
        self, envelope: Optional[dict[str, Any]], path: Path
    ) -> Optional[dict[str, Any]]:
        if envelope is None:
            return None
        dataset_sha256 = envelope.get("dataset_sha256")
        identifier_sha256 = envelope.get("identifier_sha256")
        if not _is_sha256(dataset_sha256) or not _is_sha256(identifier_sha256):
            return None
        if path != self._alias_path(dataset_sha256, identifier_sha256):
            return None
        parsed = self._validate_alias(
            envelope,
            path,
            dataset_sha256=dataset_sha256,
            identifier_sha256=identifier_sha256,
        )
        if parsed is None:
            return None
        parsed["dataset_sha256"] = dataset_sha256
        return parsed

    def _validate_manifest_for_pruning(
        self, envelope: Optional[dict[str, Any]], path: Path
    ) -> Optional[float]:
        if envelope is None:
            return None
        dataset_sha256 = envelope.get("dataset_sha256")
        if not _is_sha256(dataset_sha256):
            return None
        if self._validate_manifest(
            envelope, path, dataset_sha256=dataset_sha256
        ) is None:
            return None
        return float(envelope["fetched_at"])

    def _remove_orphan_bodies(self, referenced: set[Path]) -> None:
        for path in self._walk_cache_files(self._namespace_dir / "data"):
            if path not in referenced:
                self._discard_file(path)

    def _namespace_size(self) -> int:
        total = 0
        for root_name in ("aliases", "data", "manifests"):
            for path in self._walk_cache_files(self._namespace_dir / root_name):
                try:
                    total += path.lstat().st_size
                except OSError:
                    pass
        return total

    def _walk_cache_files(self, root: Path) -> Iterator[Path]:
        try:
            info = root.lstat()
        except (FileNotFoundError, OSError):
            return
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            return
        try:
            entries = sorted(os.scandir(root), key=lambda entry: entry.name)
        except OSError:
            return
        for entry in entries:
            path = Path(entry.path)
            try:
                if entry.is_symlink():
                    if entry.name.endswith(_JSON_SUFFIX):
                        yield path
                    continue
                if entry.is_dir(follow_symlinks=False):
                    yield from self._walk_cache_files(path)
                elif entry.is_file(follow_symlinks=False) and entry.name.endswith(
                    _JSON_SUFFIX
                ):
                    yield path
            except OSError:
                continue

    def _clear_cache_files(self, root: Path) -> int:
        removed = 0
        for path in list(self._walk_cache_files(root)):
            if self._discard_file(path):
                removed += 1
        return removed

    def _remove_empty_dirs(self, root: Path) -> None:
        try:
            info = root.lstat()
        except (FileNotFoundError, OSError):
            return
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            return
        try:
            children = sorted(os.scandir(root), key=lambda entry: entry.name)
        except OSError:
            return
        for child in children:
            if child.is_dir(follow_symlinks=False) and not child.is_symlink():
                self._remove_empty_dirs(Path(child.path))
        if root == self._namespace_dir:
            return
        try:
            root.rmdir()
        except OSError:
            pass


def _bounded_text(
    value: Any,
    name: str,
    *,
    allow_whitespace: bool = False,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value or (not allow_whitespace and not value.strip()):
        raise ValueError(f"{name} must not be empty")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{name} is not valid Unicode") from exc
    if len(encoded) > _MAX_TEXT_BYTES:
        raise ValueError(f"{name} is too long")
    return value


def _component_digest(domain: str, value: str) -> str:
    digest = hashlib.sha256()
    digest.update(b"datawiser-file-cache-v2\x00")
    digest.update(domain.encode("ascii"))
    digest.update(b"\x00")
    digest.update(value.encode("utf-8"))
    return digest.hexdigest()


def _etag_digest(etag: Optional[str]) -> str:
    return _component_digest("etag", etag if etag is not None else "<no-etag>")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _HEX_256_RE.fullmatch(value) is not None


def _valid_timestamp(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and float(value) >= 0
    )


def _payload_security_id(data: dict[str, Any]) -> Optional[str]:
    """Mirror the supported response models' security-ID locations."""

    direct = data.get("securityId")
    if isinstance(direct, str) and direct:
        return direct
    dw_ids = data.get("dwIds")
    if isinstance(dw_ids, dict):
        nested = dw_ids.get("securityId")
        if isinstance(nested, str) and nested:
            return nested
    return None


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
