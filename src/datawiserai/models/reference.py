from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class CompanyInfo:
    """Company-level metadata."""

    name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    phone_number: Optional[str] = None
    tin: Optional[str] = None
    auditor_name: Optional[str] = None
    auditor_location: Optional[str] = None

    @classmethod
    def _from_dict(cls, d: dict[str, Any] | None) -> CompanyInfo | None:
        if not d:
            return None
        return cls(
            name=d.get("name"),
            address=d.get("address"),
            city=d.get("city"),
            state=d.get("state"),
            zip=d.get("zip"),
            phone_number=d.get("phoneNumber"),
            tin=d.get("tin"),
            auditor_name=d.get("auditorName"),
            auditor_location=d.get("auditorLocation"),
        )


@dataclass(frozen=True)
class SecurityInfo:
    """Security-level metadata."""

    name: Optional[str] = None
    ticker: Optional[str] = None
    security_type: Optional[str] = None
    security_class: Optional[str] = None
    exchange_name: Optional[str] = None
    normalized_sec_type: Optional[str] = None
    security_12b_title: Optional[str] = None

    @classmethod
    def _from_dict(cls, d: dict[str, Any] | None) -> SecurityInfo | None:
        if not d:
            return None
        return cls(
            name=d.get("name"),
            ticker=d.get("ticker"),
            security_type=d.get("securityType"),
            security_class=d.get("securityClass"),
            exchange_name=d.get("exchangeName"),
            normalized_sec_type=d.get("normalizedSecType"),
            security_12b_title=d.get("Security12bTitle"),
        )


@dataclass(frozen=True)
class Reference:
    """Reference / identifier data for a single security.

    This endpoint has two possible JSON layouts depending on the ticker.
    Common fields are exposed as typed attributes; the full raw payload
    is always available via :attr:`raw`.

    Attributes
    ----------
    ticker : str
    security_id : str
    company_name : str or None
    cik : str or None
    lei : str or None
    ccy : str or None
    mic : str or None
    is_primary : bool or None
    company_info : CompanyInfo or None
    security_info : SecurityInfo or None
    raw : dict
        The original unmodified JSON payload.
    """

    ticker: str
    security_id: str
    company_name: Optional[str] = None
    cik: Optional[str] = None
    lei: Optional[str] = None
    ccy: Optional[str] = None
    mic: Optional[str] = None
    is_primary: Optional[bool] = None
    company_info: Optional[CompanyInfo] = None
    security_info: Optional[SecurityInfo] = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def _from_dict(cls, d: dict[str, Any]) -> Reference:
        ids = d.get("identifiers", {})
        dw_ids = d.get("dwIds", {})
        enhanced = d.get("enhanced", {})

        ticker = (
            d.get("ticker")
            or ids.get("tkr")
            or enhanced.get("primaryTicker", "")
        )
        security_id = d.get("securityId") or dw_ids.get("securityId", "")

        company_name = d.get("companyName") or ids.get("nameFigi")
        cik = d.get("cik") or ids.get("cik")
        lei = d.get("lei") or ids.get("lei")
        ccy = d.get("ccy") or ids.get("ccy")
        mic = d.get("mic") or ids.get("mic")
        is_primary = d.get("isPrimary") or enhanced.get("isPrimary")

        return cls(
            ticker=ticker,
            security_id=security_id,
            company_name=company_name,
            cik=cik,
            lei=lei,
            ccy=ccy,
            mic=mic,
            is_primary=is_primary,
            company_info=CompanyInfo._from_dict(d.get("companyInfo")),
            security_info=SecurityInfo._from_dict(d.get("securityInfo")),
            raw=d,
        )
