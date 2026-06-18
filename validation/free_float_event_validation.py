"""Validate free-float-events responses from the live Datawiser API."""

from __future__ import annotations

import argparse
import logging
import multiprocessing as mp
import os
import sys
import timeit
import traceback
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import datawiserai as dw
from datawiserai.models.free_float_events import expand_free_float_events_payload

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

API_KEY = os.environ["DATAWISER_API_KEY"]
TICKER = os.environ.get("DATAWISER_TICKER", "OLP")

client = dw.Client(api_key=API_KEY, use_cache=False)

logger = logging.getLogger("datawiserai.validation.free_float_event_validation")


def _validation_worker(payload: tuple[str, int, int]) -> dict[str, Any]:
    ticker, idx, total = payload
    checks = FreeFloatEventValidation(api_key=API_KEY, tickers=ticker, workers=1)
    security_id = "unknown-security-id"
    try:
        security_id = checks.security_id_for_ticker(ticker)
        return checks.ground_up_validation_worker(ticker, idx=idx, total=total)
    except Exception:
        return {
            "ticker": ticker,
            "security_id": security_id,
            "failures": [
                f"{ticker} | {security_id} worker error\n"
                f"{traceback.format_exc()}"
            ],
        }


class FreeFloatEventValidation:
    """Validation harness for live API free-float-events payloads."""

    def __init__(
        self,
        *,
        api_key: str = API_KEY,
        tickers: str | None = TICKER,
        workers: int = 1,
    ) -> None:
        self.client = dw.Client(api_key=api_key)
        self.tickers = tickers
        self.workers = workers

    @staticmethod
    def _as_decimal(value: Any) -> Decimal:
        if value in (None, ""):
            return Decimal(0)
        return Decimal(str(value))

    @classmethod
    def _owner_component_share_sum(cls, owner_component: dict[str, Any]) -> Decimal:
        return sum(
            (
                cls._as_decimal(component.get("shares"))
                for component in owner_component.get("components") or []
            ),
            Decimal(0),
        )

    @staticmethod
    def _manifest_entry_identity(key: str, entry: dict[str, Any]) -> tuple[str, str]:
        export_file = entry.get("export_file")
        if export_file:
            return ("export_file", export_file)

        security_id = entry.get("security_id")
        if security_id:
            return ("security_id", security_id)

        return ("manifest_key", key)

    @staticmethod
    def _is_full_export_entry(key: str, entry: dict[str, Any]) -> bool:
        export_file = entry.get("export_file", "")
        export_stem = entry.get("export_stem", "")
        return (
            export_file.endswith("-full.json.gz")
            or export_file.endswith("-full.json")
            or export_stem.endswith("-full")
            or key.endswith("-full")
        )

    def _raw_free_float_events(self, ticker: str) -> dict[str, Any]:
        return self.client._fetch("free-float-events", ticker)

    def _expanded_free_float_events(self, ticker: str) -> list[dict[str, Any]]:
        raw_payload = self._raw_free_float_events(ticker)
        expanded = expand_free_float_events_payload(raw_payload)
        events = list(expanded.get("events") or [])
        if raw_payload.get("eventFormat"):
            events.reverse()
        return events

    def _excluded_owner_rows_dataframe(self, ticker: str, ffe) -> pd.DataFrame:
        df_ffe = ffe.to_dataframe()
        if "is_excluded" in df_ffe.columns:
            return df_ffe[df_ffe["is_excluded"]]

        rows = []
        saw_is_excluded = False
        for event in self._expanded_free_float_events(ticker):
            as_of = pd.to_datetime(event["asOf"])
            for component in (event.get("components") or {}).values():
                if "isExcluded" in component:
                    saw_is_excluded = True
                if component.get("isExcluded", True) is False:
                    continue
                rows.append({"as_of": as_of, "shares": component.get("shares", 0.0)})

        if saw_is_excluded and rows:
            return pd.DataFrame(rows)

        return df_ffe[
            ~df_ffe["entity_type"].isin(["passive_investor", "cross_holding"])
        ]

    def _owner_component_reconciliation_dataframe(self, ticker: str) -> pd.DataFrame:
        rows = []
        for event in self._expanded_free_float_events(ticker):
            as_of = pd.to_datetime(event["asOf"])
            for owner_id, owner_component in (event.get("components") or {}).items():
                if owner_component.get("isExcluded", True) is False:
                    continue
                owner_shares = self._as_decimal(owner_component.get("shares"))
                component_shares_sum = self._owner_component_share_sum(owner_component)
                rows.append(
                    {
                        "as_of": as_of,
                        "owner_identity_id": owner_id,
                        "name": owner_component.get("name"),
                        "entity_type": owner_component.get("entityType"),
                        "source_event": owner_component.get("sourceEvent"),
                        "owner_shares": float(owner_shares),
                        "component_shares_sum": float(component_shares_sum),
                        "split_adjustment": owner_component.get("splitAdjustment"),
                        "component_count": len(
                            owner_component.get("components") or []
                        ),
                    }
                )
        return pd.DataFrame(rows)

    def security_id_for_ticker(
        self, ticker: str, endpoint: str = "free-float-events"
    ) -> str:
        manifest = self.client._get_manifest(endpoint)
        active_security_ids = {
            entry["security_id"]
            for key, entry in manifest.items()
            if entry.get("ticker") == ticker
            and not entry.get("is_delisted", False)
            and not self._is_full_export_entry(key, entry)
            and entry.get("security_id")
        }
        if len(active_security_ids) == 1:
            return next(iter(active_security_ids))
        if active_security_ids:
            return ",".join(sorted(active_security_ids))
        return "unknown-security-id"

    def _tickers_to_check(self) -> list[str]:
        universe = self.client.universe("free-float-events")
        if not self.tickers:
            return list(dict.fromkeys(universe.tickers))

        requested = [ticker.strip() for ticker in self.tickers.split(",")]
        requested = [ticker for ticker in requested if ticker]
        missing = [ticker for ticker in requested if ticker not in universe]
        if missing:
            raise AssertionError(
                f"Tickers not found in free-float-events universe: {missing}"
            )
        return requested

    def check_universe_exists(self) -> bool:
        universe = self.client.universe("free-float-events")
        if not universe.tickers:
            raise AssertionError("No tickers found in free-float-events universe")
        logger.info(
            "Universe exists for free-float-events with %s entries",
            len(universe.entries),
        )
        return True

    def check_duplicate_tickers_have_one_active_security(self) -> bool:
        failures = []
        manifest = self.client._get_manifest("free-float-events")
        entries_by_ticker: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(
            list
        )
        for key, entry in manifest.items():
            entries_by_ticker[entry["ticker"]].append((key, entry))

        for ticker, entries in entries_by_ticker.items():
            if len(entries) <= 1:
                continue

            active_securities = set()
            for key, entry in entries:
                if entry.get("is_delisted", False):
                    continue
                if self._is_full_export_entry(key, entry):
                    continue
                active_securities.add(self._manifest_entry_identity(key, entry))

            if len(active_securities) != 1:
                failures.append(
                    f"free-float-events/{ticker}: {len(entries)} manifest entries, "
                    f"{len(active_securities)} active securities"
                )

        if failures:
            sample = "\n".join(failures[:20])
            extra = "" if len(failures) <= 20 else f"\n... plus {len(failures) - 20} more"
            raise AssertionError(
                "Duplicate ticker check failed. Recycled tickers must have exactly "
                f"one active non-delisted security.\n{sample}{extra}"
            )

        logger.info("Duplicate ticker check passed for free-float-events manifest")
        return True

    def ground_up_validation_worker(
        self, ticker: str, *, idx: int = 1, total: int = 1
    ) -> dict[str, Any]:
        failures = []
        security_id = self.security_id_for_ticker(ticker)
        check_label = f"{ticker} | {security_id}"
        logger.info(
            "Checking ground-up free-float validation for %s (%s/%s)",
            check_label,
            idx,
            total,
        )

        ffe = self.client.free_float_events(ticker)
        df_events = ffe.to_event_summary_dataframe()
        if df_events.empty:
            failures.append(f"{check_label} has no event summary rows")
            return {
                "ticker": ticker,
                "security_id": security_id,
                "failures": failures,
            }

        # The top-level free-float factor should be a direct calculation:
        # 1 - excluded_shares / shares_out. This proves the summary rows carry
        # enough top-level state for a client to recalculate ff_factor.
        df_events["ff2"] = 1 - df_events["excluded_shares"] / df_events["shares_out"]
        ff_factor_msk = df_events["ff2"].round(6) == df_events["ff_factor"].round(6)
        if not ff_factor_msk.all():
            cols = ["as_of", "excluded_shares", "shares_out", "ff_factor", "ff2"]
            failures.append(
                f"{check_label} ff_factor mismatch\n"
                f"{df_events.loc[~ff_factor_msk, cols].to_string()}"
            )

        # delta_shares should explain the movement from the next event's
        # excluded_shares back to the current event's excluded_shares. The
        # client-facing event summary is sorted most-recent first, so the
        # previous state is the next row in this DataFrame.
        df_events["excluded_shares_before_delta"] = (
            df_events["excluded_shares"] - df_events["delta_shares"]
        )
        df_events["next_excluded_shares"] = df_events["excluded_shares"].shift(-1)
        df_events["delta_check_diff"] = (
            df_events["excluded_shares_before_delta"]
            - df_events["next_excluded_shares"]
        )
        delta_msk = (
            df_events["excluded_shares_before_delta"].round(6)
            == df_events["next_excluded_shares"].round(6)
        )
        delta_msk.iloc[-1] = True
        if not delta_msk.all():
            cols = [
                "as_of",
                "excluded_shares",
                "delta_shares",
                "excluded_shares_before_delta",
                "next_excluded_shares",
                "delta_check_diff",
                "shares_out",
                "is_rebal",
            ]
            failures.append(
                f"{check_label} delta_shares mismatch\n"
                f"{df_events.loc[~delta_msk, cols].to_string()}"
            )

        # Public owner rows marked as excluded are contribution-ready. A client
        # should be able to sum excluded owner shares for each date and land
        # exactly on the top-level excluded_shares.
        df_ffe_excluded = self._excluded_owner_rows_dataframe(ticker, ffe)
        df_owner_sums = (
            df_ffe_excluded.groupby("as_of", as_index=False)["shares"]
            .sum()
            .rename(columns={"shares": "owner_shares_sum"})
        )
        df_events_with_owner_sums = df_events.merge(
            df_owner_sums,
            on="as_of",
            how="left",
        )
        df_events_with_owner_sums["owner_sum_diff"] = (
            df_events_with_owner_sums["owner_shares_sum"]
            - df_events_with_owner_sums["excluded_shares"]
        )
        owner_sum_msk = (
            df_events_with_owner_sums["owner_shares_sum"].round(6)
            == df_events_with_owner_sums["excluded_shares"].round(6)
        )
        if not owner_sum_msk.all():
            cols = [
                "as_of",
                "excluded_shares",
                "owner_shares_sum",
                "owner_sum_diff",
            ]
            bad_owner_sums = df_events_with_owner_sums.loc[~owner_sum_msk, cols]
            failures.append(
                f"{check_label} owner share sum mismatch "
                f"({len(bad_owner_sums)} rows)\n"
                f"{bad_owner_sums.head(20).to_string()}"
            )

        # Nested owner components are also contribution-ready. First check each
        # owner's component rows sum to that owner row's shares. This validates
        # the nested drill-down shape exposed by free_float_events_detail().
        df_owner_component_sums = self._owner_component_reconciliation_dataframe(ticker)
        if df_owner_component_sums.empty:
            failures.append(f"{check_label} owner component rows missing")
            return {
                "ticker": ticker,
                "security_id": security_id,
                "failures": failures,
            }

        df_owner_component_sums["owner_component_sum_diff"] = (
            df_owner_component_sums["component_shares_sum"]
            - df_owner_component_sums["owner_shares"]
        )
        owner_component_msk = (
            df_owner_component_sums["component_shares_sum"].round(6)
            == df_owner_component_sums["owner_shares"].round(6)
        )
        if not owner_component_msk.all():
            cols = [
                "as_of",
                "owner_identity_id",
                "name",
                "entity_type",
                "source_event",
                "owner_shares",
                "component_shares_sum",
                "owner_component_sum_diff",
                "split_adjustment",
                "component_count",
            ]
            bad_owner_components = df_owner_component_sums.loc[
                ~owner_component_msk, cols
            ]
            failures.append(
                f"{check_label} owner component sum mismatch "
                f"({len(bad_owner_components)} rows)\n"
                f"{bad_owner_components.head(20).to_string()}"
            )

        # Then sum every nested component across all owners for the event. This
        # is the strongest JSON-feed proof: sum(sum(owner.components.shares))
        # should equal the top-level excluded_shares.
        df_component_sums = (
            df_owner_component_sums.groupby("as_of", as_index=False)[
                "component_shares_sum"
            ]
            .sum()
            .rename(columns={"component_shares_sum": "component_shares_sum"})
        )
        df_events_with_component_sums = df_events.merge(
            df_component_sums,
            on="as_of",
            how="left",
        )
        df_events_with_component_sums["component_sum_diff"] = (
            df_events_with_component_sums["component_shares_sum"]
            - df_events_with_component_sums["excluded_shares"]
        )
        component_sum_msk = (
            df_events_with_component_sums["component_shares_sum"].round(6)
            == df_events_with_component_sums["excluded_shares"].round(6)
        )
        if not component_sum_msk.all():
            cols = [
                "as_of",
                "excluded_shares",
                "component_shares_sum",
                "component_sum_diff",
            ]
            bad_component_sums = df_events_with_component_sums.loc[
                ~component_sum_msk, cols
            ]
            failures.append(
                f"{check_label} component share sum mismatch "
                f"({len(bad_component_sums)} rows)\n"
                f"{bad_component_sums.head(20).to_string()}"
            )

        return {
            "ticker": ticker,
            "security_id": security_id,
            "failures": failures,
        }

    def ground_up_validation(self) -> bool:
        start_time = timeit.default_timer()
        failures = []
        affected_tickers = set()
        affected_security_ids = set()
        tickers = self._tickers_to_check()
        workers = min(max(int(self.workers or 1), 1), len(tickers)) if tickers else 1
        logger.info(
            "Running ground-up free-float validation for %s tickers with %s worker(s)",
            len(tickers),
            workers,
        )

        if workers <= 1:
            results = [
                self.ground_up_validation_worker(ticker, idx=idx, total=len(tickers))
                for idx, ticker in enumerate(tickers, start=1)
            ]
        else:
            payloads = [
                (ticker, idx, len(tickers))
                for idx, ticker in enumerate(tickers, start=1)
            ]
            results = []
            ctx = mp.get_context("spawn")
            with ctx.Pool(processes=workers) as pool:
                for result in pool.imap_unordered(
                    _validation_worker,
                    payloads,
                    chunksize=1,
                ):
                    results.append(result)
                    logger.info(
                        "Completed ground-up validation for %s | %s",
                        result["ticker"],
                        result["security_id"],
                    )

        for result in results:
            result_failures = result["failures"]
            if not result_failures:
                continue
            affected_tickers.add(result["ticker"])
            affected_security_ids.add(result["security_id"])
            failures.extend(result_failures)

        if failures:
            sample = "\n\n".join(failures[:20])
            extra = "" if len(failures) <= 20 else f"\n\n... plus {len(failures) - 20} more"
            affected_tickers_text = ",".join(sorted(affected_tickers))
            affected_security_ids_text = ",".join(sorted(affected_security_ids))
            raise AssertionError(
                "Ground-up free-float validation failed.\n"
                f"Affected security_ids ({len(affected_security_ids)}): "
                f"{affected_security_ids_text}\n\n"
                f"Affected tickers ({len(affected_tickers)}): "
                f"{affected_tickers_text}\n\n"
                f"{sample}{extra}"
            )

        logger.info(
            "Ground-up free-float validation passed for %s tickers in %.2fs",
            len(tickers),
            timeit.default_timer() - start_time,
        )
        return True

    def run(self) -> dict[str, bool]:
        return {
            "universe_exists": self.check_universe_exists(),
            "duplicate_tickers_have_one_active_security": (
                self.check_duplicate_tickers_have_one_active_security()
            ),
            "ground_up_validation": self.ground_up_validation(),
        }


def main(tickers: str | None = None, workers: int = 4) -> dict[str, bool]:
    checks = FreeFloatEventValidation(tickers=tickers, workers=workers)
    return checks.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Validate live free-float-events API responses."
    )
    parser.add_argument(
        "--tickers",
        help="Comma-separated tickers/security_ids to validate. Defaults to All tickers in the universe.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of worker processes. Defaults to 4.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    main(tickers=args.tickers, workers=args.workers)
