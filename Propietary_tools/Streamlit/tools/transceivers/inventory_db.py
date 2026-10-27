"""MADIT | Transceivers inventory storage (Google Sheets).

Autonomous module: it only reads/writes one worksheet of one Google Sheet and knows
nothing about Quotes, Clients or Calendar. The same Sheet is the data source of the
AppSheet mobile app, so both sides see each other's changes.

Needs these Streamlit secrets:

    [gsheets]
    spreadsheet_id = "1AbC...the id between /d/ and /edit in the Sheet URL"
    worksheet = "Inventory"            # optional, default "Inventory"

    [gcp_service_account]              # the full JSON key of the service account
    type = "service_account"
    project_id = "..."
    private_key_id = "..."
    private_key = "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n"
    client_email = "...@...iam.gserviceaccount.com"
    client_id = "..."
    token_uri = "https://oauth2.googleapis.com/token"

The Sheet must be shared (Editor) with the service account's client_email.

Layout: ONE ROW PER MODULE, `serial` is the unique key. Nested data is flattened:

    serial, part_number, status, model, vendor, profile_code, client, device, device_type,
    module_bay, location, rack, asset_tag, installed_on, description, comments,
    tags (comma-separated), image (URL),
    flex_part_number, flex_product, flex_full_sku, flex_form_factor, flex_compliance,
    flex_distance, flex_wavelength_tx, flex_temp_range, flex_connector, flex_fibre,
    flex_delivery_slip, flex_delivery_date, flex_order, flex_order_date,
    flex_customer_reference, flexoptix_json (any other flexoptix key),
    created, updated, updated_by

Writes touch ONLY the cells that change in the row of that serial (never the whole
sheet), so edits made from the phone and from Streamlit do not overwrite each other
unless they change the same cell. Extra columns added by hand (e.g. by AppSheet) are
left alone. The inventory is cached in st.session_state for CACHE_TTL seconds, so
changes made from the phone show up in Streamlit after that time or after
load_inventory(force=True).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import gspread
import requests
import streamlit as st
from google.oauth2.service_account import Credentials
from gspread.utils import rowcol_to_a1

DEFAULT_TAB = "Inventory"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

CACHE_KEY = "fx_inventory_cache"
CACHE_TTL = 30          # seconds before Streamlit re-reads the Sheet by itself
MAX_ATTEMPTS = 3

STATUSES = ["In stock", "Reserved", "Delivered to client", "Installed", "Spare", "Faulty / RMA", "Retired"]

# Fields the user edits by hand (everything else comes from the Flexoptix API).
# "image" is the product photo URL (from the Flexoptix products API).
TEXT_FIELDS = (
    "status", "vendor", "profile_code", "client", "device", "device_type", "module_bay",
    "location", "rack", "asset_tag", "installed_on", "description", "comments", "model",
    "image", "part_number",
)

# Keys of the `flexoptix` snapshot that get their own column (flex_<key>)
FLEX_KEYS = (
    "part_number", "product", "full_sku", "form_factor", "compliance", "distance",
    "wavelength_tx", "temp_range", "connector", "fibre",
    "delivery_slip", "delivery_date", "order", "order_date", "customer_reference",
)
FLEX_PREFIX = "flex_"
SYSTEM_COLS = ("created", "updated", "updated_by")

COLUMNS = [
    "serial", "part_number", "status", "model", "vendor", "profile_code", "client", "device", "device_type",
    "module_bay", "location", "rack", "asset_tag", "installed_on", "description", "comments",
    "tags", "image",
    *[FLEX_PREFIX + k for k in FLEX_KEYS],
    "flexoptix_json",
    *SYSTEM_COLS,
]


class InventoryError(Exception):
    """Problem reading/writing the inventory (configuration, Google Sheets, conflicts)."""


# ── Google Sheets plumbing ─────────────────────────────────────────────────────
def _explain(code: int) -> str:
    if code == 403:
        return ("Google Sheets: access denied (403). Share the Sheet with the service account "
                "email (client_email) as Editor, and check that the Sheets API is enabled.")
    if code == 404:
        return "Google Sheets: spreadsheet not found (404). Check spreadsheet_id in the secrets."
    if code == 429:
        return "Google Sheets: too many requests (429). Wait a minute and try again."
    return f"Google Sheets error (HTTP {code})."


def _retry(fn, *args, **kwargs):
    """Calls a gspread function, retrying on rate limits / temporary server errors."""
    for attempt in range(MAX_ATTEMPTS):
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as exc:
            code = getattr(exc.response, "status_code", 0)
            if code in (429, 500, 502, 503) and attempt < MAX_ATTEMPTS - 1:
                time.sleep(2 * (2 ** attempt))
                continue
            raise InventoryError(_explain(code)) from exc
        except requests.RequestException as exc:
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(1)
                continue
            raise InventoryError(f"Network error talking to Google Sheets: {exc}") from exc


@st.cache_resource(show_spinner=False)
def _worksheet():
    """The inventory worksheet (created if the tab does not exist). Cached per process."""
    try:
        cfg = st.secrets["gsheets"]
        sheet_id = str(cfg["spreadsheet_id"])
        tab = str(cfg.get("worksheet", DEFAULT_TAB))
        info = dict(st.secrets["gcp_service_account"])
    except Exception as exc:
        raise InventoryError(
            "Google Sheets secrets not found. You need a [gsheets] section (spreadsheet_id, "
            "optional worksheet) and a [gcp_service_account] section with the JSON key."
        ) from exc

    try:
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        spreadsheet = gspread.authorize(creds).open_by_key(sheet_id)
    except gspread.exceptions.APIError as exc:
        raise InventoryError(_explain(getattr(exc.response, "status_code", 0))) from exc
    except Exception as exc:
        raise InventoryError(f"Could not connect to Google Sheets: {exc}") from exc

    try:
        return spreadsheet.worksheet(tab)
    except gspread.exceptions.WorksheetNotFound:
        return _retry(spreadsheet.add_worksheet, title=tab, rows=1000, cols=len(COLUMNS))


def _ensure_header(ws, header: list[str]) -> list[str]:
    """Header row of the sheet, adding any of our columns that are missing at the end.

    Columns that somebody added by hand are kept where they are.
    """
    header = [h.strip() for h in header]
    while header and not header[-1]:
        header.pop()
    missing = [c for c in COLUMNS if c not in header]
    if missing:
        header = header + missing
        if len(header) > ws.col_count:
            _retry(ws.add_cols, len(header) - ws.col_count)
        _retry(ws.update, range_name="A1", values=[header], value_input_option="RAW")
    return header


def _load(ws) -> tuple[list[str], list[list[str]]]:
    """(header, data rows). Values come back as text, so serials keep their zeros."""
    values = _retry(ws.get_all_values)
    header = _ensure_header(ws, values[0] if values else [])
    return header, values[1:]


def _invalidate() -> None:
    st.session_state.pop(CACHE_KEY, None)


# ── Row <-> record conversion ──────────────────────────────────────────────────
def _cell(value) -> str:
    return "" if value is None else str(value)


def _to_record(d: dict) -> dict:
    rec = {f: d.get(f, "") for f in TEXT_FIELDS}
    rec["serial"] = d.get("serial", "").strip()
    rec["tags"] = [t.strip() for t in d.get("tags", "").split(",") if t.strip()]

    fx: dict = {}
    extra = d.get("flexoptix_json", "").strip()
    if extra:
        try:
            loaded = json.loads(extra)
            if isinstance(loaded, dict):
                fx.update(loaded)
        except ValueError:
            pass
    for key in FLEX_KEYS:
        value = d.get(FLEX_PREFIX + key, "")
        if value != "":
            fx[key] = value
    rec["flexoptix"] = fx
    if not rec["part_number"]:              # older rows: use the Flexoptix one
        rec["part_number"] = fx.get("part_number", "")

    for col in SYSTEM_COLS:
        rec[col] = d.get(col, "")
    return rec


def _to_cells(changes: dict) -> dict[str, str]:
    """{column: text} for a (partial) record. `flexoptix` replaces the whole snapshot."""
    cells: dict[str, str] = {}
    for key, value in changes.items():
        if key == "flexoptix":
            fx = value or {}
            for k in FLEX_KEYS:
                cells[FLEX_PREFIX + k] = _cell(fx.get(k, ""))
            extra = {k: v for k, v in fx.items() if k not in FLEX_KEYS}
            cells["flexoptix_json"] = json.dumps(extra, ensure_ascii=False) if extra else ""
        elif key == "tags":
            cells["tags"] = ", ".join(str(t) for t in (value or []))
        else:
            cells[key] = _cell(value)
    return cells


def _find_rows(ws, header: list[str], serials) -> dict[str, int]:
    """{serial: 1-based sheet row} for the serials that exist (header row excluded)."""
    wanted = set(serials)
    column = _retry(ws.col_values, header.index("serial") + 1)
    found: dict[str, int] = {}
    for row_number, value in enumerate(column, start=1):
        value = value.strip()
        if row_number > 1 and value in wanted and value not in found:
            found[value] = row_number
    return found


# ── Helpers ────────────────────────────────────────────────────────────────────
def _now() -> str:
    # "YYYY-MM-DD HH:MM:SS" (UTC): easy for AppSheet and for humans reading the Sheet
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _user() -> str:
    return str(st.session_state.get("username", "") or "")


def new_record(serial: str, flexoptix: dict | None = None, **fields) -> dict:
    """A complete module record with defaults; `fields` override the manual fields."""
    now = _now()
    rec = {f: "" for f in TEXT_FIELDS}
    rec.update({
        "serial": serial,
        "status": "In stock",
        "tags": [],
        "flexoptix": dict(flexoptix or {}),
        "created": now,
        "updated": now,
        "updated_by": _user(),
    })
    for key, value in fields.items():
        if key in TEXT_FIELDS or key == "tags":
            rec[key] = value
    # Part number priority: 1) the one given (e.g. from the uploaded CSV),
    # 2) the one from the Flexoptix serial API, 3) empty (typed by hand later)
    rec["part_number"] = rec["part_number"] or (flexoptix or {}).get("part_number", "")
    return rec


# ── Public API ─────────────────────────────────────────────────────────────────
def load_inventory(force: bool = False) -> dict:
    """Returns {serial: record}. Cached CACHE_TTL seconds; force=True re-reads the Sheet."""
    cached = st.session_state.get(CACHE_KEY)
    if force or cached is None or time.time() - cached["ts"] > CACHE_TTL:
        header, rows = _load(_worksheet())
        modules: dict = {}
        for row in rows:
            d = {h: (row[i] if i < len(row) else "") for i, h in enumerate(header)}
            serial = d.get("serial", "").strip()
            if serial and serial not in modules:   # first row wins if a serial is repeated
                modules[serial] = _to_record(d)
        cached = {"modules": modules, "ts": time.time()}
        st.session_state[CACHE_KEY] = cached
    return cached["modules"]


def add_modules(records: dict[str, dict]) -> tuple[int, int]:
    """Appends records whose serial is not in the Sheet yet (one request).

    Existing modules are never touched. Returns (added, already_present).
    """
    ws = _worksheet()
    header, rows = _load(ws)
    serial_idx = header.index("serial")
    existing = {r[serial_idx].strip() for r in rows if len(r) > serial_idx}

    new_rows: list[list[str]] = []
    present = 0
    for serial, rec in records.items():
        if serial in existing:
            present += 1
            continue
        cells = _to_cells(rec)
        cells["serial"] = serial
        new_rows.append([cells.get(h, "") for h in header])
        existing.add(serial)

    if new_rows:
        _retry(ws.append_rows, new_rows, value_input_option="RAW", table_range="A1")
    _invalidate()
    return len(new_rows), present


def save_module(serial: str, changes: dict) -> None:
    """Updates only the changed cells of one module and stamps updated / updated_by."""
    ws = _worksheet()
    header = _ensure_header(ws, _retry(ws.row_values, 1))
    row = _find_rows(ws, header, [serial]).get(serial)
    if row is None:
        raise InventoryError(f"{serial} is not in the inventory (it may have been deleted).")

    cells = _to_cells(changes)
    cells.pop("serial", None)               # the key never changes
    cells["updated"] = _now()
    cells["updated_by"] = _user()

    data = [
        {"range": rowcol_to_a1(row, header.index(col) + 1), "values": [[value]]}
        for col, value in cells.items() if col in header
    ]
    _retry(ws.batch_update, data, value_input_option="RAW")
    _invalidate()


def delete_module(serial: str) -> None:
    delete_modules([serial])


def delete_modules(serials) -> int:
    """Deletes several modules in ONE request. Returns how many were actually removed."""
    serials = list(serials)
    ws = _worksheet()
    header = _ensure_header(ws, _retry(ws.row_values, 1))
    rows = sorted(_find_rows(ws, header, serials).values(), reverse=True)  # bottom first
    if not rows:
        _invalidate()
        return 0

    requests_body = [
        {"deleteDimension": {"range": {
            "sheetId": ws.id, "dimension": "ROWS", "startIndex": r - 1, "endIndex": r,
        }}}
        for r in rows
    ]
    _retry(ws.spreadsheet.batch_update, {"requests": requests_body})
    _invalidate()
    return len(rows)
