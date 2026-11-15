"""Company-based relationship assertions; no ticker or website identity mapping."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
import re
from typing import Any, Iterator, Optional
from urllib.parse import urlsplit

from .._exceptions import ManifestCompatibilityError

_INVERSE = {"supplier": "customer", "customer": "supplier", "competitor": "competitor",
            "partner": "partner", "unspecified": "unspecified"}
_STATUSES = {"current", "historical", "planned", "terminated", "unknown"}
_DIRECTNESS = {"direct", "indirect", "unspecified"}
_ASSERTION_COLUMNS = [
    "relationship_id", "company_id", "company_name", "related_company_id", "related_company_name",
    "reporting_company_id", "reporting_company_name", "counterparty_company_id", "counterparty_company_name",
    "relationship_type", "view_relationship_type", "relationship_subtype", "status", "directness",
    "scope_key", "product_service", "filing_date", "relationship_start_date", "relationship_end_date",
    "document_id", "source_url", "source_form",
]
_EVIDENCE_COLUMNS = _ASSERTION_COLUMNS + [
    "evidence_id", "text", "page_label", "section_path", "location", "evidence_source_url", "source_tag",
]
_METRIC_COLUMNS = _ASSERTION_COLUMNS + [
    "metric_id", "evidence_id", "metric_type", "value", "raw_value", "value_qualifier", "basis_party",
    "basis_company_id", "basis_description", "currency_code", "period_start", "period_end", "period_label",
]


def _text(d, key):
    value = d[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError("missing text: " + key)
    return value


def _id(d, key):
    value = _text(d, key)
    if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) is None:
        raise ValueError("invalid ID: " + key)
    return value


def _date(d, key, optional=False):
    value = d.get(key) if optional else d[key]
    if value is None and optional:
        return None
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("invalid date: " + key)
    date.fromisoformat(value)
    return value


@dataclass(frozen=True)
class RelationshipCompany:
    company_id: str
    name: str

    @classmethod
    def _from_dict(cls, d):
        return cls(_id(d, "companyId"), _text(d, "name"))


@dataclass(frozen=True)
class RelationshipEvidence:
    evidence_id: str
    text: str
    page_label: Optional[str]
    section_path: Optional[str]
    location: dict[str, int]
    source_url: Optional[str] = None
    source_tag: Optional[str] = None

    @classmethod
    def _from_dict(cls, d):
        location = d.get("location", {})
        if not isinstance(location, dict) or any(type(v) is not int or v < 0 for v in location.values()):
            raise ValueError("invalid evidence location")
        source_url = d.get("sourceUrl")
        if source_url is not None:
            parsed = urlsplit(source_url)
            if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("invalid evidence source URL")
        return cls(_id(d, "evidenceId"), _text(d, "text"), d.get("pageLabel"), d.get("sectionPath"),
                   dict(location), source_url, d.get("sourceTag"))


@dataclass(frozen=True)
class RelationshipMetric:
    metric_id: str
    evidence_id: str
    metric_type: str
    value: Decimal
    raw_value: str
    value_qualifier: str
    basis_party: str
    basis_company_id: str
    basis_description: str
    currency_code: Optional[str]
    period_start: Optional[str]
    period_end: Optional[str]
    period_label: Optional[str]

    @classmethod
    def _from_dict(cls, d):
        raw = d["value"]
        if not isinstance(raw, str) or re.fullmatch(r"(0|[1-9][0-9]*)(\.[0-9]+)?", raw) is None:
            raise ValueError("metric value must be a nonnegative decimal string")
        value = Decimal(raw)
        kind = d["metricType"]
        currency = d.get("currencyCode")
        if kind in {"revenue_share", "purchase_share"}:
            if value > 1 or currency is not None:
                raise ValueError("invalid share metric")
        elif kind not in {"revenue_amount", "purchase_amount", "contract_value", "order_value"} or not isinstance(currency, str) or re.fullmatch(r"[A-Z]{3}", currency) is None:
            raise ValueError("invalid monetary metric")
        if d["valueQualifier"] not in {"eq", "approx", "gt", "gte", "lt", "lte"} or d["basisParty"] not in {"reporting_company", "counterparty"}:
            raise ValueError("invalid metric qualifier or basis")
        start, end = _date(d, "periodStart", True), _date(d, "periodEnd", True)
        if start and end and start > end:
            raise ValueError("reversed metric period")
        return cls(_id(d, "metricId"), _id(d, "evidenceId"), kind, value, _text(d, "rawValue"),
                   d["valueQualifier"], d["basisParty"], _id(d, "basisCompanyId"), _text(d, "basisDescription"),
                   currency, start, end, d.get("periodLabel"))


@dataclass(frozen=True)
class CompanyRelationship:
    relationship_id: str
    reporting_company: RelationshipCompany
    counterparty: RelationshipCompany
    relationship_type: str
    view_relationship_type: str
    relationship_subtype: str
    directness: str
    status: str
    scope_key: str
    product_service: Optional[str]
    filing_date: str
    document_id: str
    source_url: str
    source_form: Optional[str]
    relationship_start_date: Optional[str]
    relationship_end_date: Optional[str]
    evidence: tuple[RelationshipEvidence, ...]
    metrics: tuple[RelationshipMetric, ...]

    @classmethod
    def _from_dict(cls, d):
        reporter = RelationshipCompany._from_dict(d["reportingCompany"])
        other = RelationshipCompany._from_dict(d["counterparty"])
        if reporter.company_id == other.company_id or d["relationshipType"] not in _INVERSE:
            raise ValueError("invalid relationship endpoints or role")
        if d["directness"] not in {"direct", "indirect", "unspecified"} or d["status"] not in _STATUSES:
            raise ValueError("invalid relationship vocabulary")
        if not isinstance(d["evidence"], list) or not isinstance(d["metrics"], list):
            raise ValueError("invalid assertion details")
        evidence = tuple(RelationshipEvidence._from_dict(e) for e in d["evidence"])
        metrics = tuple(RelationshipMetric._from_dict(m) for m in d["metrics"])
        ids = {e.evidence_id for e in evidence}
        if not evidence or len(ids) != len(evidence) or len({m.metric_id for m in metrics}) != len(metrics):
            raise ValueError("missing or duplicate evidence/metrics")
        for metric in metrics:
            basis = reporter if metric.basis_party == "reporting_company" else other
            if metric.evidence_id not in ids or metric.basis_company_id != basis.company_id:
                raise ValueError("metric provenance mismatch")
        source = d["source"]
        url = _text(source, "url")
        parsed = urlsplit(url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("invalid source URL")
        start, end = _date(d, "relationshipStartDate", True), _date(d, "relationshipEndDate", True)
        if start and end and start > end:
            raise ValueError("reversed relationship dates")
        return cls(_id(d, "relationshipId"), reporter, other, d["relationshipType"], d["viewRelationshipType"],
                   d.get("relationshipSubtype", ""), d["directness"], d["status"], d.get("scopeKey", ""),
                   d.get("productService"), _date(d, "filingDate"), _id(source, "documentId"), url, source.get("form"),
                   start, end, evidence, metrics)


@dataclass(frozen=True)
class CompanyRelationships:
    """Published assertions viewed from one company, optionally filtered locally.

    ``selection`` describes the server's publication policy and ``raw`` is the
    original response. Local filters change only ``relationships``; they perform
    no requests and preserve both the source response and metric provenance.
    """
    company: RelationshipCompany
    relationships: tuple[CompanyRelationship, ...]
    selection: dict[str, Any]
    raw: dict[str, Any] = field(repr=False)

    @property
    def company_id(self):
        return self.company.company_id

    def __len__(self) -> int:
        return len(self.relationships)

    def __iter__(self) -> Iterator[CompanyRelationship]:
        return iter(self.relationships)

    def filter(
        self, *, role: Optional[str] = None, status: Optional[str] = None,
        directness: Optional[str] = None,
    ) -> CompanyRelationships:
        """Return a local view matching all supplied criteria.

        ``role`` uses ``view_relationship_type``: supplier means a supplier of
        the requested company, including disclosures made by the other endpoint.
        Values are case-sensitive. Omitted criteria impose no additional filter;
        chained calls narrow the existing view. Publication selection and the
        original ``raw`` response remain available unchanged.
        """
        for label, value, allowed in (("role", role, _INVERSE), ("status", status, _STATUSES),
                                       ("directness", directness, _DIRECTNESS)):
            if value is not None and (not isinstance(value, str) or value not in allowed):
                raise ValueError(f"{label} must be one of: {', '.join(sorted(allowed))}")
        rows = tuple(row for row in self.relationships
                     if (role is None or row.view_relationship_type == role)
                     and (status is None or row.status == status)
                     and (directness is None or row.directness == directness))
        return replace(self, relationships=rows)

    def suppliers(self, *, status: Optional[str] = None, directness: Optional[str] = None) -> CompanyRelationships:
        """Return published supplier assertions, with optional status/directness filters."""
        return self.filter(role="supplier", status=status, directness=directness)

    def customers(self, *, status: Optional[str] = None, directness: Optional[str] = None) -> CompanyRelationships:
        """Return published customer assertions, with optional status/directness filters."""
        return self.filter(role="customer", status=status, directness=directness)

    def competitors(self, *, status: Optional[str] = None, directness: Optional[str] = None) -> CompanyRelationships:
        """Return published competitor assertions, with optional status/directness filters."""
        return self.filter(role="competitor", status=status, directness=directness)

    def partners(self, *, status: Optional[str] = None, directness: Optional[str] = None) -> CompanyRelationships:
        """Return published partner assertions, with optional status/directness filters."""
        return self.filter(role="partner", status=status, directness=directness)

    @classmethod
    def _from_dict(cls, d):
        if d["schemaVersion"] != "company-relationships-v1" or not isinstance(d["relationships"], list):
            raise ValueError("unsupported company relationships contract")
        company = RelationshipCompany._from_dict(d["company"])
        selection = d["selection"]
        if (selection["kind"] != "latest_disclosed" or selection["resolvedOnly"] is not True
                or selection["knowledgeTimeMode"] != "current_source_current_knowledge"
                or type(selection["includeUnspecified"]) is not bool
                or not isinstance(selection["statuses"], list) or set(selection["statuses"]) - _STATUSES):
            raise ValueError("unsupported relationship selection")
        as_of = _date(selection, "asOf", True)
        rows = tuple(CompanyRelationship._from_dict(r) for r in d["relationships"])
        if len({r.relationship_id for r in rows}) != len(rows):
            raise ValueError("duplicate assertion")
        for row in rows:
            if company.company_id == row.reporting_company.company_id:
                endpoint, expected = row.reporting_company, row.relationship_type
            elif company.company_id == row.counterparty.company_id:
                endpoint, expected = row.counterparty, _INVERSE[row.relationship_type]
            else:
                raise ValueError("assertion does not involve requested company")
            if endpoint != company or row.view_relationship_type != expected:
                raise ValueError("incorrect company perspective")
            if as_of and row.filing_date > as_of:
                raise ValueError("assertion after as-of date")
            if row.relationship_type == "unspecified" and not selection["includeUnspecified"]:
                raise ValueError("unspecified relationship not selected")
            if selection["statuses"] and row.status not in selection["statuses"]:
                raise ValueError("status not selected")
        return cls(company, rows, dict(selection), d)

    def _assertion_record(self, row: CompanyRelationship) -> dict[str, Any]:
        related = row.counterparty if self.company_id == row.reporting_company.company_id else row.reporting_company
        return {
            "relationship_id": row.relationship_id, "company_id": self.company_id, "company_name": self.company.name,
            "related_company_id": related.company_id, "related_company_name": related.name,
            "reporting_company_id": row.reporting_company.company_id,
            "reporting_company_name": row.reporting_company.name,
            "counterparty_company_id": row.counterparty.company_id,
            "counterparty_company_name": row.counterparty.name,
            "relationship_type": row.relationship_type, "view_relationship_type": row.view_relationship_type,
            "relationship_subtype": row.relationship_subtype,
            "status": row.status, "directness": row.directness, "product_service": row.product_service,
            "scope_key": row.scope_key, "filing_date": row.filing_date,
            "relationship_start_date": row.relationship_start_date, "relationship_end_date": row.relationship_end_date,
            "document_id": row.document_id, "source_url": row.source_url, "source_form": row.source_form,
        }

    def to_dataframe(self):
        """One row per assertion, retaining repeated disclosures and original roles.

        ``related_company_id`` always identifies the other endpoint relative to
        ``company_id``. Date columns retain published ISO strings. Empty views
        have the same columns as nonempty views. Requires the pandas extra.
        """
        import pandas as pd
        return pd.DataFrame([self._assertion_record(row) for row in self.relationships],
                            columns=_ASSERTION_COLUMNS)

    def evidence_dataframe(self):
        """One row per evidence passage, linked to its assertion and document.

        Passage URLs/tags and locations remain separate from the document URL.
        Text is untruncated; empty views retain all columns. Requires pandas.
        """
        import pandas as pd
        records = []
        for row in self.relationships:
            context = self._assertion_record(row)
            for evidence in row.evidence:
                records.append({**context, "evidence_id": evidence.evidence_id, "text": evidence.text,
                                "page_label": evidence.page_label, "section_path": evidence.section_path,
                                "location": dict(evidence.location), "evidence_source_url": evidence.source_url,
                                "source_tag": evidence.source_tag})
        return pd.DataFrame(records, columns=_EVIDENCE_COLUMNS)

    def metrics_dataframe(self):
        """One row per accepted metric, retaining Decimal values and original basis.

        Share fractions, currencies, qualifiers and periods are preserved as
        published. Metrics join evidence using relationship_id/evidence_id; no
        aggregation or perspective-based rebasing is applied. Requires pandas.
        """
        import pandas as pd
        records = []
        for row in self.relationships:
            context = self._assertion_record(row)
            for metric in row.metrics:
                records.append({**context, **vars(metric)})
        return pd.DataFrame(records, columns=_METRIC_COLUMNS)


@dataclass(frozen=True)
class CompanyUniverseEntry:
    company_id: str
    name: Optional[str]
    last_update: str
    etag: str


@dataclass(frozen=True)
class CompanyUniverse:
    endpoint: str
    entries: tuple[CompanyUniverseEntry, ...]

    @classmethod
    def _from_manifest(cls, endpoint, manifest):
        try:
            entries = []
            for key, value in sorted(manifest.items()):
                if key != _id(value, "company_id"):
                    raise ValueError("company manifest identity mismatch")
                updated = _text(value, "last_update")
                if datetime.fromisoformat(updated.replace("Z", "+00:00")).tzinfo is None:
                    raise ValueError("naive last update")
                name = value.get("company_name")
                if name is not None and not isinstance(name, str):
                    raise ValueError("invalid company name")
                if endpoint == "company-relationships" and (name is None or not name.strip()):
                    raise ValueError("relationship discovery requires company names")
                entries.append(CompanyUniverseEntry(key, name, updated, _text(value, "etag")))
            return cls(endpoint, tuple(entries))
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ManifestCompatibilityError(endpoint, "invalid company update entries") from exc

    @property
    def company_ids(self):
        return [entry.company_id for entry in self.entries]

    def __iter__(self):
        return iter(self.entries)

    def __len__(self):
        return len(self.entries)

    def __contains__(self, company_id):
        return company_id in self.company_ids

    def to_dataframe(self):
        import pandas as pd
        return pd.DataFrame([vars(entry) for entry in self.entries],
                            columns=["company_id", "name", "last_update", "etag"])
