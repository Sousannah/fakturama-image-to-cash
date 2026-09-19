"""Second verification layer: read the persisted records straight out of the DB.

Why this exists
---------------
Reading a field back through UIA proves that the *widget* shows the right text.
It does not prove the record was committed. Querying the database afterwards is
the only oracle that proves the flow really produced a saved Order and a linked
Invoice with the right values.

Why there is no JDBC here
-------------------------
Recon on the live installation showed Fakturama 2.2.0 ships **HSQLDB 2.7.4**,
not H2 (the workspace holds `Database.script` / `.log` / `.properties` /
`.lobs`). HSQLDB keeps MEMORY tables as plain-text SQL in `Database.script`,
and everything committed since the last checkpoint as plain-text SQL in
`Database.log`. So the oracle is a parser, not a driver:

* no JDBC jar, no JPype, no extra dependency
* no exclusive-lock problem - both files are read-only opened
* uncommitted-to-script rows are still visible, because `.log` is parsed too

The trade-off is that this reads HSQLDB's on-disk format rather than asking the
engine. That format is stable and documented, and the parser fails loudly
rather than silently returning nothing.
"""
from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..config import SETTINGS
from ..logging_setup import get
from ..models import OrderDoc

log = get("verify.db")

TOLERANCE = Decimal("0.01")

_CREATE_RE = re.compile(
    r'CREATE\s+(?:MEMORY|CACHED|TEXT)\s+TABLE\s+(?:PUBLIC\.)?"?([A-Z_0-9]+)"?\s*\(',
    re.IGNORECASE,
)
_INSERT_RE = re.compile(
    r'INSERT\s+INTO\s+(?:PUBLIC\.)?"?([A-Z_0-9]+)"?\s+VALUES\s*\(',
    re.IGNORECASE,
)
_NON_COLUMN = ("CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "KEY")


# --------------------------------------------------------------------------- #
# locating the workspace
# --------------------------------------------------------------------------- #
def find_database(workspace: Optional[Path] = None) -> Optional[Path]:
    """Return the `Database.script` path for the configured workspace."""
    roots: List[Path] = []
    if workspace:
        roots.append(Path(workspace))
    if SETTINGS.workspace:
        roots.append(Path(SETTINGS.workspace))
    roots.append(Path.home() / "Fakturama2")

    for root in roots:
        for candidate in (root / "Database" / "Database.script", root / "Database.script"):
            if candidate.exists():
                return candidate
        hits = sorted(root.glob("**/Database.script")) if root.exists() else []
        if hits:
            return hits[0]
    return None


# --------------------------------------------------------------------------- #
# HSQLDB text parsing
# --------------------------------------------------------------------------- #
def _split_top_level(text: str, separator: str = ",") -> List[str]:
    """Split on `separator`, ignoring anything inside quotes or parentheses."""
    parts, current, depth, i = [], [], 0, 0
    in_string = False
    while i < len(text):
        ch = text[i]
        if in_string:
            if ch == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    current.append("''")
                    i += 2
                    continue
                in_string = False
            current.append(ch)
        elif ch == "'":
            in_string = True
            current.append(ch)
        elif ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == separator and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    parts.append("".join(current))
    return parts


def _balanced_body(text: str, open_index: int) -> str:
    """Return the contents of the parenthesis group starting at `open_index`."""
    depth, i, in_string = 0, open_index, False
    start = open_index + 1
    while i < len(text):
        ch = text[i]
        if in_string:
            if ch == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    i += 2
                    continue
                in_string = False
        elif ch == "'":
            in_string = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[start:i]
        i += 1
    return text[start:]


def parse_columns(script_text: str) -> Dict[str, List[str]]:
    """table name -> ordered column names, from the CREATE TABLE statements."""
    tables: Dict[str, List[str]] = {}
    for match in _CREATE_RE.finditer(script_text):
        table = match.group(1).upper()
        body = _balanced_body(script_text, match.end() - 1)
        columns: List[str] = []
        for part in _split_top_level(body):
            part = part.strip()
            if not part or part.upper().startswith(_NON_COLUMN):
                continue
            name = re.match(r'"?([A-Z_0-9]+)"?', part, re.IGNORECASE)
            if name:
                columns.append(name.group(1).upper())
        tables[table] = columns
    return tables


def _parse_value(raw: str) -> Any:
    raw = raw.strip()
    if not raw or raw.upper() == "NULL":
        return None
    if raw.startswith("'") and raw.endswith("'") and len(raw) >= 2:
        return raw[1:-1].replace("''", "'")
    if raw.upper() in ("TRUE", "FALSE"):
        return raw.upper() == "TRUE"
    try:
        return Decimal(raw)
    except Exception:
        return raw


def parse_rows(text: str, columns: Dict[str, List[str]], wanted: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Collect INSERT rows for `wanted` tables as column-keyed dicts."""
    wanted_upper = {w.upper() for w in wanted}
    out: Dict[str, List[Dict[str, Any]]] = {w: [] for w in wanted_upper}

    for match in _INSERT_RE.finditer(text):
        table = match.group(1).upper()
        if table not in wanted_upper:
            continue
        body = _balanced_body(text, match.end() - 1)
        values = [_parse_value(v) for v in _split_top_level(body)]
        names = columns.get(table) or []
        if names and len(names) == len(values):
            out[table].append(dict(zip(names, values)))
        else:
            out[table].append({"_positional": values})
    return out


def load_tables(
    wanted: Sequence[str], script_path: Optional[Path] = None
) -> Dict[str, List[Dict[str, Any]]]:
    """Read `wanted` tables from Database.script plus the uncheckpointed log."""
    script_path = script_path or find_database()
    if script_path is None:
        raise RuntimeError(
            "no Fakturama database found under %s (set F2C_WORKSPACE)" % SETTINGS.workspace
        )

    script_text = Path(script_path).read_text(encoding="utf-8", errors="replace")
    columns = parse_columns(script_text)
    rows = parse_rows(script_text, columns, wanted)

    log_path = Path(script_path).with_suffix(".log")
    if log_path.exists():
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        extra = parse_rows(log_text, columns, wanted)
        for table, items in extra.items():
            rows[table].extend(items)
        log.debug(
            "read %s (+%d row(s) from the uncheckpointed log)",
            script_path.name,
            sum(len(v) for v in extra.values()),
        )

    for table, items in rows.items():
        log.debug("  %s: %d row(s)", table, len(items))
    return rows


# --------------------------------------------------------------------------- #
# the actual assertions
# --------------------------------------------------------------------------- #
def _decimal(value) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _alive(row: Dict[str, Any]) -> bool:
    return not bool(row.get("DELETED"))


def verify_against_database(doc: OrderDoc) -> Dict[str, Any]:
    """Assert the Order, its linked Invoice and the master data were persisted."""
    problems: List[str] = []
    found: Dict[str, Any] = {}

    tables = load_tables(
        ["FKT_DOCUMENT", "FKT_CONTACT", "FKT_PRODUCT", "FKT_VAT", "FKT_PAYMENT", "FKT_DOCUMENTITEM"]
    )

    # --- documents ---------------------------------------------------------
    documents = [
        r for r in tables["FKT_DOCUMENT"]
        if _alive(r) and str(r.get("CUSTOMERREF") or "").strip() == doc.external_ref
    ]
    found["documents"] = [
        {
            "dtype": r.get("DTYPE"),
            "name": r.get("NAME"),
            "customerref": r.get("CUSTOMERREF"),
            "total": str(r.get("TOTALVALUE")),
            "paid": r.get("PAID"),
            "paidvalue": str(r.get("PAIDVALUE")),
            "paydate": str(r.get("PAYDATE")),
        }
        for r in documents
    ]

    if not documents:
        problems.append("no document row with CUSTOMERREF %r" % doc.external_ref)
    else:
        types = {str(r.get("DTYPE") or "").casefold() for r in documents}
        if not any("order" in t for t in types):
            problems.append("no Order document persisted (saw %s)" % sorted(types))
        if not any("invoice" in t for t in types):
            problems.append("no Invoice document persisted (saw %s)" % sorted(types))

        for row in documents:
            total = _decimal(row.get("TOTALVALUE"))
            if total is not None and abs(total - Decimal(doc.gross_total)) > TOLERANCE:
                problems.append(
                    "%s total %s != expected %s"
                    % (row.get("DTYPE"), total, doc.gross_total)
                )
            if "invoice" in str(row.get("DTYPE") or "").casefold():
                paid = bool(row.get("PAID"))
                if paid != doc.is_paid:
                    problems.append("invoice PAID is %s, expected %s" % (paid, doc.is_paid))
                if doc.is_paid:
                    value = _decimal(row.get("PAIDVALUE"))
                    if value is not None and abs(value - Decimal(doc.gross_total)) > TOLERANCE:
                        problems.append(
                            "invoice PAIDVALUE %s != invoice total %s" % (value, doc.gross_total)
                        )

    # --- contact -----------------------------------------------------------
    contacts = [
        r for r in tables["FKT_CONTACT"]
        if _alive(r) and str(r.get("COMPANY") or "").strip().casefold() == doc.company.casefold()
    ]
    found["contacts"] = [
        {"company": r.get("COMPANY"), "first": r.get("FIRSTNAME"), "name": r.get("NAME")}
        for r in contacts
    ]
    if not contacts:
        problems.append("no contact row for company %r" % doc.company)
    elif len(contacts) > 1:
        problems.append("%d contact rows for company %r" % (len(contacts), doc.company))

    # --- products / VATs ---------------------------------------------------
    found["products"] = []
    for item in doc.items:
        matches = [
            r for r in tables["FKT_PRODUCT"]
            if _alive(r)
            and str(r.get("ITEMNUMBER") or "").strip().casefold() == item.sku.casefold()
        ]
        found["products"].append(
            {
                "sku": item.sku,
                "rows": [{"name": r.get("NAME"), "price1": str(r.get("PRICE1"))} for r in matches],
            }
        )
        if not matches:
            problems.append("no product row with ITEMNUMBER %r" % item.sku)
        elif len(matches) > 1:
            problems.append("%d product rows with ITEMNUMBER %r" % (len(matches), item.sku))

    vats = [r for r in tables["FKT_VAT"] if _alive(r)]
    found["vats"] = [{"name": r.get("NAME"), "value": str(r.get("TAXVALUE"))} for r in vats]
    for rate in doc.distinct_vat_rates():
        name = "VAT %s%%" % format(Decimal(rate).normalize(), "f")
        if not any(str(r.get("NAME") or "").strip().casefold() == name.casefold() for r in vats):
            problems.append("no VAT row named %r" % name)

    payments = [r for r in tables["FKT_PAYMENT"] if _alive(r)]
    found["payments"] = [{"name": r.get("NAME"), "code": r.get("CODE")} for r in payments]
    if not any(
        str(r.get("NAME") or "").strip().casefold() == doc.payment_method.casefold()
        for r in payments
    ):
        problems.append("no payment method named %r" % doc.payment_method)

    return {
        "ok": not problems,
        "problems": problems,
        "found": found,
        "summary": "database verification passed"
        if not problems
        else "%d problem(s): %s" % (len(problems), "; ".join(problems)),
    }
