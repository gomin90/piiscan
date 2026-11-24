"""Function B input model: DB metadata field auto-detection and user mapping.

Metadata extracts differ per DBMS and per extraction tool, so the physical
header names of the input file are resolved against canonical roles
(table_name, column_name, data_type, ...). Headers that map to no role are
reported rather than silently dropped, and any role may be overridden.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Canonical role -> accepted header aliases (normalised: upper, non-alnum removed).
ALIASES: dict[str, tuple[str, ...]] = {
    "table_name": (
        "TABLENAME", "TABLE", "TBL", "TBLNAME", "TABLENM", "OBJECTNAME", "ENTITYNAME", "RELATION",
        "테이블명", "테이블",
    ),
    "table_comment": (
        "TABLECOMMENT", "TABLEDESC", "TABLEDESCRIPTION", "TABLEINFO", "TABLECOMMENTS",
        "테이블설명", "테이블주석",
    ),
    "column_name": (
        "COLUMNNAME", "COLUMN", "COL", "COLNAME", "FIELD", "FIELDNAME", "ATTRIBUTE", "ATTR",
        "컬럼명", "컬럼",
    ),
    "column_comment": (
        "COLUMNCOMMENT", "COLUMNDESC", "COLCOMMENT", "COLDESC", "FIELDDESC", "COMMENT", "DESCRIPTION",
        "컬럼설명", "컬럼주석", "설명",
    ),
    "data_type": (
        "DATATYPE", "TYPE", "COLUMNTYPE", "FIELDTYPE", "DTYPE", "SQLTYPE",
        "자료형", "데이터타입", "타입",
    ),
    "column_length": (
        "COLUMNLENGTH", "LENGTH", "SIZE", "MAXLENGTH", "PRECISION", "LEN", "컬럼길이", "길이",
    ),
    "nullable": ("NULLABLE", "ISNULL", "ALLOWNULL", "NULLALLOWED", "NOTNULL"),
    "primary_key": ("PRIMARYKEY", "PK", "ISPK", "KEYTYPE", "KEY", "UNIQUE"),
    "foreign_key": ("FOREIGNKEY", "FK", "ISFK", "REFERENCE", "REF"),
    "schema_name": ("SCHEMANAME", "SCHEMA", "DATASET"),
    "db_name": ("DBNAME", "DATABASENAME", "DATABASE", "CATALOG"),
}

# A metadata row is a column definition, so table + column are mandatory.
REQUIRED_ROLES = ("table_name", "column_name")
ALL_ROLES = tuple(ALIASES)


def _normalise(name: str) -> str:
    return re.sub(r"[^0-9A-Za-zÀ-ÿ\uAC00-\uD7A3]+", "", str(name or "")).upper()


def detect_field_mapping(headers: list[str]) -> dict[str, dict]:
    """Resolve canonical roles to input headers.

    Exact alias matches win over substring matches; each header is bound to at
    most one role so two roles cannot collapse onto the same column.
    """
    mapping: dict[str, dict] = {}
    taken: set[int] = set()

    for role, aliases in ALIASES.items():
        norms = {_normalise(alias) for alias in aliases}
        best = None
        for index, header in enumerate(headers):
            if index in taken:
                continue
            norm = _normalise(header)
            if not norm:
                continue
            if norm in norms:
                best = {"header": header, "index": index, "confidence": "exact"}
                break
            if best is None and any(len(alias) >= 6 and alias in norm for alias in norms):
                best = {"header": header, "index": index, "confidence": "alias"}
        if best:
            mapping[role] = best
            taken.add(best["index"])
    return mapping


@dataclass
class FieldMapping:
    """Resolved (and optionally user-corrected) mapping of input headers."""

    headers: list[str]
    mapping: dict[str, dict] = field(default_factory=dict)
    auto_mapping: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def from_headers(cls, headers: list[str]) -> "FieldMapping":
        auto = detect_field_mapping(headers)
        return cls(headers=list(headers), mapping=dict(auto), auto_mapping=dict(auto))

    def set(self, role: str, header: str | None) -> None:
        """Override (or clear) the header bound to a role."""
        if header is None:
            self.mapping.pop(role, None)
            return
        index = self.headers.index(header) if header in self.headers else None
        self.mapping[role] = {"header": header, "index": index, "confidence": "manual"}

    def value(self, row: list[str], role: str, default: str = "") -> str:
        entry = self.mapping.get(role)
        if not entry or entry["index"] is None or entry["index"] >= len(row):
            return default
        return (row[entry["index"]] or "").strip()

    def unmapped(self) -> list[str]:
        used = {entry["header"] for entry in self.mapping.values()}
        return [header for header in self.headers if header not in used]

    def validate(self) -> tuple[bool, str]:
        missing = [role for role in REQUIRED_ROLES if role not in self.mapping]
        if missing:
            return False, "필드 매핑 없음: " + ", ".join(missing)
        return True, ""

    def describe(self) -> dict:
        return {
            "auto_detected": {role: entry["header"] for role, entry in self.auto_mapping.items()},
            "applied": {role: entry["header"] for role, entry in self.mapping.items()},
            "unmapped": self.unmapped(),
            "confidence": {role: entry["confidence"] for role, entry in self.auto_mapping.items()},
        }
