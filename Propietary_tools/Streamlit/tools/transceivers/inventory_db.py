"""MADIT | Transceivers inventory storage (Google Sheets).

One row per module in a tab of a Google Sheet. Streamlit and, later, an AppSheet mobile
app read and write the same sheet. Autonomous module: it knows nothing about Quotes,
Clients or Calendar.

Streamlit secrets needed:

    [gcp_service_account]            # the service-account JSON key, field by field
    type = "service_account"
    project_id = "..."
    private_key_id = "..."
    private_key = "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n"
    client_email = "...@....iam.gserviceaccount.com"
    client_id = "..."
    token_uri = "https://oauth2.googleapis.com/token"

    [inventory_sheet]
    spreadsheet_id = "..."           # the long id in the sheet's URL
    worksheet = "Inventory"          # optional, default "Inventory"

and the sheet must be shared (Editor) with the service account's client_email.

How it behaves
--------------
* The header row is the contract. Columns are found BY NAME, so the sheet can have extra
  columns, a different column order, or be re-sorted by AppSheet without breaking anything.
  If the tab is empty the header row is created; if some of our headers are missing they are
  added at the END (existing columns are never moved).
* Streamlit never rewrites the sheet. It appends new rows and updates only the cells that
  changed in the row whose Serial matches (the row is looked up at write time, so row
  numbers may shift between reads). That way edits made from the phone in the meantime are
  not overwritten.
* Values are written as plain text (RAW), so serials like 0012345 or text starting with "="
  stay exactly as typed.
* The inventory is cached in st.session_state; load_inventory(force=True) re-reads it.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import gspread
import streamlit as st
from gspread.exceptions import APIError, WorksheetNotFound
from gspread.utils import rowcol_to_a1

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
DEFAULT_TAB = "Inventory"
CACHE_KEY = "fx_inventory_cache"
RETRIES = 3

STATUSES = ["In stock", "Reserved", "Delivered to client", "Installed", "Spare", "Faulty / RMA", "Retired"]

# Fields the user edits by hand (everything else comes from the Flexoptix API)
TEXT_FIELDS = (
    "status", "vendor", "profile_code", "client", "device", "device_type", "module_bay",
    "location", "rack", "asset_tag", "installed_on", "description", "comments", "model",
)

# (record key, sheet header). "fx.<name>" is a field of the Flexoptix snapshot.
COLUMNS = [
    ("serial", "Serial"),
    ("status", "Status"),
    ("vendor", "Vendor"),
    ("profile_code", "Profile code"),
    ("client", "Client"),
    ("device", "Device"),
    ("device_type", "Device type"),
    ("module_bay", "Port"),
    ("location", "Location"),
    ("rack", "Rack"),
    ("asset_tag", "Asset tag"),
    ("installed_on", "Installed on"),
    ("description", "Description"),
    ("comments", "Comments"),
    ("tags", "Tags"),
    ("model", "Model"),
    ("fx.part_number", "Part number"),
    ("fx.full_sku", "Full SKU"),
    ("fx.product", "Product"),
    ("fx.form_factor", "Form factor"),
    ("fx.compliance", "Compliance"),
    ("fx.distance", "Distance"),
    ("fx.wavelength_tx", "Wavelength TX"),
    ("fx.temp_range", "Temp range"),
    ("fx.connector", "Connector"),
    ("fx.fibre", "Fibre"),
    ("fx.order", "Order"),
    ("fx.order_date", "Order date"),
    ("fx.delivery_slip", "Delivery slip"),
    ("fx.delivery_date", "Delivery date"),
    ("fx.customer_reference", "Customer ref"),
    ("created", "Created"),
    ("updated", "Updated"),
    ("updated_by", "Updated by"),
]
HEADERS = [h for _, h in COLUMNS]
HEADER_BY_KEY = {k: h for k, h in COLUMNS}
KEY_BY_HEADER = {h: k for k, h in COLUMNS}


class InventoryError(Exception):
    """Problem reading/writing the inventory (configuration, permissions, missing data)."""


# ── Google connection ──────────────────────────────────────────────────────────
def _sa_email() -> str:
    try:
        return str(st.secrets["gcp_service_account"]["client_email"])
    except Exception:
        return "the service account"


@st.cache_resource(ttl=3000, show_spinner=False)
def _client():
    try:
        info = dict(st.secrets["gcp_service_account"])
    except Exception as exc:
        raise InventoryError(
            "Secrets section [gcp_service_account] not found. Paste the service-account JSON "
            "key there, field by field."
        ) from exc
    try:
        return gspread.service_account_from_dict(info, scopes=SCOPES)
    except Exception as exc:
        raise InventoryError(f"The Google service-account key is not valid: {exc}") from exc


def _sheet_cfg() -> tuple[str, str]:
    try:
        cfg = st.secrets["inventory_sheet"]
        spreadsheet_id = str(cfg["spreadsheet_id"]).strip()
        tab = str(cfg.get("worksheet", "") or DEFAULT_TAB).strip()
    except Exception as exc:
        raise InventoryError(
            "Secrets section [inventory_sheet] with spreadsheet_id not found."
        ) from exc
    if not spreadsheet_id:
        raise InventoryError("inventory_sheet.spreadsheet_id is empty.")
    return spreadsheet_id, tab


def _retry(fn):
    """Runs a Google call, retrying on rate limits / transient errors, and turning the
    common permission problems into readable messages."""
    for attempt in range(RETRIES):
        try:
            return fn()
        except APIError as exc:
            code = getattr(getattr(exc, "response", None), "status_code", None)
            if code in (429, 500, 502, 503) and attempt < RETRIES - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            if code == 403:
                raise InventoryError(
                    f"Google refused access (403). Share the sheet with {_sa_email()} as Editor, "
                    "and check that the Google Sheets API is enabled in the Google Cloud project."
                ) from exc
            if code == 404:
                raise InventoryError(
                    f"Spreadsheet not found (404). Check inventory_sheet.spreadsheet_id and that "
                    f"the sheet is shared with {_sa_email()}."
                ) from exc
            raise


@st.cache_resource(ttl=3000, show_spinner=False)
def _worksheet(spreadsheet_id: str, tab: str):
    sh = _retry(lambda: _client().open_by_key(spreadsheet_id))
    try:
        return _retry(lambda: sh.worksheet(tab))
    except WorksheetNotFound:
        return _retry(lambda: sh.add_worksheet(title=tab, rows=1000, cols=len(HEADERS) + 5))


def _open():
    """Returns (worksheet, header, values); creates / completes the header row if needed."""
    spreadsheet_id, tab = _sheet_cfg()
    ws = _worksheet(spreadsheet_id, tab)
    values = _retry(ws.get_all_values) or []

    if not values or not any(str(c).strip() for c in values[0]):
        _ensure_columns(ws, len(HEADERS))
        _retry(lambda: ws.batch_update([{"range": "A1", "values": [HEADERS]}], value_input_option="RAW"))
        values = [list(HEADERS)] + (values[1:] if values else [])

    header = [str(c).strip() for c in values[0]]
    missing = [h for h in HEADERS if h not in header]
    if missing:
        start = len(header) + 1
        _ensure_columns(ws, start + len(missing) - 1)
        _retry(lambda: ws.batch_update(
            [{"range": rowcol_to_a1(1, start), "values": [missing]}], value_input_option="RAW"))
        header += missing
        values[0] = header
    return ws, header, values


def _ensure_columns(ws, needed: int) -> None:
    if ws.col_count < needed:
        _retry(lambda: ws.resize(cols=needed))


# ── Row <-> record ─────────────────────────────────────────────────────────────
def _scalar(key: str, value) -> str:
    if key == "tags":
        return ", ".join(value or []) if isinstance(value, (list, tuple)) else str(value or "")
    return "" if value is None else str(value)


def _value_for(rec: dict, key: str) -> str:
    if key.startswith("fx."):
        return _scalar(key, (rec.get("flexoptix") or {}).get(key[3:]))
    return _scalar(key, rec.get(key))


def _row_values(header: list[str], rec: dict) -> list[str]:
    return [_value_for(rec, KEY_BY_HEADER[h]) if h in KEY_BY_HEADER else "" for h in header]


def _record_from_row(header: list[str], row: list) -> dict:
    cells = {h: (str(row[i]) if i < len(row) else "") for i, h in enumerate(header) if h}
    rec: dict = {"flexoptix": {}}
    for key, h in COLUMNS:
        val = cells.get(h, "")
        if key.startswith("fx."):
            if val != "":
                rec["flexoptix"][key[3:]] = val
        elif key == "tags":
            rec["tags"] = [t.strip() for t in val.split(",") if t.strip()]
        else:
            rec[key] = val.strip() if key == "serial" else val
    return rec


def _find_row(header: list[str], values: list[list], serial: str) -> int | None:
    """1-based sheet row number of the module, looked up now (rows may have moved)."""
    idx = header.index("Serial")
    for n, row in enumerate(values[1:], start=2):
        if idx < len(row) and str(row[idx]).strip() == serial:
            return n
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _user() -> str:
    return str(st.session_state.get("username", "") or "")


def _forget() -> None:
    st.session_state.pop(CACHE_KEY, None)


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
    return rec


# ── Public API ─────────────────────────────────────────────────────────────────
def load_inventory(force: bool = False) -> dict:
    """Returns {serial: record}. Cached in the session; force=True re-reads the sheet."""
    cached = st.session_state.get(CACHE_KEY)
    if cached is None or force:
        if force:
            _worksheet.clear()
        _, header, values = _open()
        idx = header.index("Serial")
        cached = {}
        for row in values[1:]:
            serial = str(row[idx]).strip() if idx < len(row) else ""
            if serial and serial not in cached:  # keep the first row if a serial is repeated
                cached[serial] = _record_from_row(header, row)
        st.session_state[CACHE_KEY] = cached
    return cached


def add_modules(records: dict[str, dict]) -> tuple[int, int]:
    """Appends records whose serial is not in the sheet yet. Existing rows are never
    touched. Returns (added, already_present)."""
    ws, header, values = _open()
    idx = header.index("Serial")
    existing = {str(r[idx]).strip() for r in values[1:] if idx < len(r)}

    new_rows, present = [], 0
    for serial, rec in records.items():
        if serial in existing:
            present += 1
        else:
            new_rows.append(_row_values(header, rec))
            existing.add(serial)
    if new_rows:
        _retry(lambda: ws.append_rows(new_rows, value_input_option="RAW"))
    _forget()
    return len(new_rows), present


def save_module(serial: str, changes: dict) -> None:
    """Writes ONLY the cells in `changes` (plus Updated / Updated by) on the row of `serial`.

    `changes` uses record keys; "flexoptix" may hold a dict of snapshot fields.
    """
    ws, header, values = _open()
    row_no = _find_row(header, values, serial)
    if row_no is None:
        raise InventoryError(f"{serial} is not in the sheet (it may have been deleted).")

    pending: dict = {}
    for key, value in changes.items():
        if key == "flexoptix":
            for fk, fv in (value or {}).items():
                if f"fx.{fk}" in HEADER_BY_KEY:
                    pending[f"fx.{fk}"] = fv
        elif key in HEADER_BY_KEY and key not in ("serial", "created"):
            pending[key] = value
    pending["updated"] = _now()
    pending["updated_by"] = _user()

    data = [
        {
            "range": rowcol_to_a1(row_no, header.index(HEADER_BY_KEY[key]) + 1),
            "values": [[_scalar(key, value)]],
        }
        for key, value in pending.items()
    ]
    _retry(lambda: ws.batch_update(data, value_input_option="RAW"))
    _forget()


def delete_module(serial: str) -> None:
    ws, header, values = _open()
    row_no = _find_row(header, values, serial)
    if row_no is not None:
        _retry(lambda: ws.delete_rows(row_no))
    _forget()


def check_connection() -> str:
    """Opens the sheet (creating the header row if the tab is empty) and describes it."""
    ws, _, values = _open()
    return (f"Connected to “{ws.spreadsheet.title}” · tab “{ws.title}” · "
            f"{max(len(values) - 1, 0)} module row(s).")
