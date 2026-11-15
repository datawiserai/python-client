"""Current company reference fields and eligible securities, without ticker lookup."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .company_relationships import _id


def _optional_text(d, key):
    value = d.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError("invalid optional text: " + key)
    return value


@dataclass(frozen=True)
class CompanyReferenceSecurity:
    security_id: str
    name: Optional[str] = None
    primary_ticker: Optional[str] = None
    security_type: Optional[str] = None

    @classmethod
    def _from_dict(cls, d):
        return cls(_id(d, "securityId"), _optional_text(d, "name"), _optional_text(d, "primaryTicker"),
                   _optional_text(d, "securityType"))


@dataclass(frozen=True)
class CompanyReference:
    company_id: str
    company_name: Optional[str]
    lei: Optional[str]
    securities: tuple[CompanyReferenceSecurity, ...]
    raw: dict[str, Any] = field(repr=False)

    @classmethod
    def _from_dict(cls, d):
        if d["schemaVersion"] != "company-reference-v1" or not isinstance(d["securities"], list):
            raise ValueError("unsupported company reference contract")
        company = d["company"]
        securities = tuple(CompanyReferenceSecurity._from_dict(s) for s in d["securities"])
        if len({s.security_id for s in securities}) != len(securities):
            raise ValueError("duplicate company reference security ID")
        lei = _optional_text(company, "lei")
        return cls(_id(company, "companyId"), _optional_text(company, "name"), lei, securities, d)

    def to_dataframe(self):
        """One row per eligible security; companies with none return an empty frame."""
        import pandas as pd
        columns = ["company_id", "company_name", "lei", "security_id", "security_name", "primary_ticker", "security_type"]
        return pd.DataFrame([{"company_id": self.company_id, "company_name": self.company_name, "lei": self.lei,
                              "security_id": security.security_id, "security_name": security.name,
                              "primary_ticker": security.primary_ticker, "security_type": security.security_type}
                             for security in self.securities], columns=columns)
