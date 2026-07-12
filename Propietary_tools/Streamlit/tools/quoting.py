import streamlit as st
import pandas as pd
import zipfile
import xml.etree.ElementTree as ET
import io
import re
import json
import pathlib
import html as _html
import xlrd
from datetime import datetime


TOKEN_FILE = pathlib.Path("/tmp/xero_tokens.json")


# ── XLSX reader ────────────────────────────────────────────────────────────────
def read_xlsx_native(file) -> dict:
    content = file.read() if hasattr(file, "read") else open(file, "rb").read()
    z = zipfile.ZipFile(io.BytesIO(content))

    shared_strings = []
    if "xl/sharedStrings.xml" in z.namelist():
        tree = ET.parse(z.open("xl/sharedStrings.xml"))
        root = tree.getroot()
        ns = root.tag.split("}")[0] + "}" if "}" in root.tag else ""
        for si in root.findall(f"{ns}si"):
            texts = si.findall(f".//{ns}t")
            shared_strings.append("".join(t.text or "" for t in texts))

    sheet_map = {}
    wb_tree = ET.parse(z.open("xl/workbook.xml"))
    wb_root = wb_tree.getroot()
    wb_ns   = wb_root.tag.split("}")[0] + "}" if "}" in wb_root.tag else ""
    rels = {}
    if "xl/_rels/workbook.xml.rels" in z.namelist():
        r_tree = ET.parse(z.open("xl/_rels/workbook.xml.rels"))
        for rel in r_tree.getroot():
            rels[rel.get("Id")] = rel.get("Target")
    for sheet in wb_root.findall(f".//{wb_ns}sheet"):
        name   = sheet.get("name")
        r_id   = sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        target = rels.get(r_id, "")
        path   = f"xl/{target}" if not target.startswith("xl/") else target
        sheet_map[name] = path

    def parse_sheet(path) -> pd.DataFrame:
        if path not in z.namelist():
            return pd.DataFrame()
        tree = ET.parse(z.open(path))
        root = tree.getroot()
        ns   = root.tag.split("}")[0] + "}" if "}" in root.tag else ""
        rows = []
        for row in root.findall(f".//{ns}row"):
            row_data = {}
            for cell in row.findall(f"{ns}c"):
                col_ref = "".join(filter(str.isalpha, cell.get("r", "")))
                col_idx = 0
                for ch in col_ref:
                    col_idx = col_idx * 26 + (ord(ch) - ord("A") + 1)
                col_idx -= 1
                v = cell.find(f"{ns}v")
                t = cell.get("t", "")
                if v is not None and v.text is not None:
                    row_data[col_idx] = shared_strings[int(v.text)] if t == "s" else v.text
                else:
                    row_data[col_idx] = ""
            rows.append(row_data)
        if not rows:
            return pd.DataFrame()
        max_col = max(max(r.keys()) for r in rows if r)
        data = [[r.get(c, "") for c in range(max_col + 1)] for r in rows]
        return pd.DataFrame(data)

    return {name: parse_sheet(path) for name, path in sheet_map.items()}


# ── XLS reader (legacy .xls format) ─────────────────────────────────────────────
def read_xls_native(file) -> dict:
    content = file.read() if hasattr(file, "read") else open(file, "rb").read()
    book = xlrd.open_workbook(file_contents=content)

    sheets = {}
    for sheet in book.sheets():
        rows = []
        for r in range(sheet.nrows):
            row_data = {}
            for c in range(sheet.ncols):
                cell = sheet.cell(r, c)
                if cell.ctype == xlrd.XL_CELL_NUMBER:
                    val = cell.value
                    if val == int(val):
                        row_data[c] = str(int(val))
                    else:
                        row_data[c] = str(val)
                elif cell.ctype == xlrd.XL_CELL_DATE:
                    dt = xlrd.xldate.xldate_as_datetime(cell.value, book.datemode)
                    row_data[c] = dt.strftime("%d/%m/%Y")
                else:
                    row_data[c] = str(cell.value) if cell.value != "" else ""
            rows.append(row_data)

        if not rows:
            sheets[sheet.name] = pd.DataFrame()
            continue

        max_col = max(max(r.keys()) for r in rows if r)
        data = [[r.get(c, "") for c in range(max_col + 1)] for r in rows]
        sheets[sheet.name] = pd.DataFrame(data)

    return sheets


# ── Unified reader dispatcher ────────────────────────────────────────────────────
def read_quote_file(file, filename: str) -> dict:
    if filename.lower().endswith(".xls") and not filename.lower().endswith(".xlsx"):
        return read_xls_native(file)
    return read_xlsx_native(file)


def extract_amount(val: str) -> float:
    cleaned = val.replace(",", "")
    match = re.search(r'\d+\.?\d*', cleaned)
    if match:
        try:
            return float(match.group())
        except ValueError:
            pass
    return 0.0


# ── Auto-detect distributor ────────────────────────────────────────────────────
def detect_distributor(sheets: dict) -> str:
    all_text = ""
    for df in sheets.values():
        for _, row in df.iterrows():
            all_text += " ".join(str(v) for v in row).lower() + " "
        if len(all_text) > 5000:
            break
    if "tech data" in all_text or "techdata" in all_text or "1300 36 25 25" in all_text:
        return "TECHDATA"
    if "nextgen" in all_text or "next gen" in all_text:
        return "NEXTGEN"
    if "general" in " ".join(sheets.keys()).lower():
        return "TECHDATA"
    return "NEXTGEN"


# ── NEXTGEN parser ─────────────────────────────────────────────────────────────
def parse_nextgen(sheets: dict) -> tuple[dict, pd.DataFrame]:
    df_raw = next(iter(sheets.values()))
    meta = {}
    try:
        row1 = df_raw.iloc[1]
        meta["quote_number"] = str(row1[1]).strip()
        meta["description"]  = str(row1[4]).strip()
        meta["expiry"]       = str(row1[7]).split(" ")[0].strip()
        meta["end_user"]     = str(row1[26]).strip() if len(row1) > 26 else ""
        meta["reseller"]     = str(row1[31]).strip() if len(row1) > 31 else ""
        meta["currency"]     = str(row1[43]).strip() if len(row1) > 43 else "AUD"
    except Exception:
        pass

    items = []
    for _, row in df_raw.iterrows():
        try:
            line_no = int(float(str(row[0])))
        except (ValueError, TypeError):
            continue
        sku         = str(row[1]).strip()
        description = str(row[2]).replace("_x000a_", " ").strip()
        try:
            qty        = int(float(str(row[5])))
            unit_price = float(str(row[6]))
            total      = float(str(row[7]))
        except (ValueError, TypeError):
            continue
        items.append({
            "#":           line_no,
            "SKU":         sku,
            "Description": description,
            "Qty":         qty,
            "Unit Cost":   unit_price,
            "Total Cost":  total,
        })
    return meta, pd.DataFrame(items)


# ── TECHDATA parser ────────────────────────────────────────────────────────────
def parse_techdata(sheets: dict) -> tuple[dict, pd.DataFrame]:
    summary_key = next((k for k in sheets if k.strip().lower() == "summary"), None)
    general_key = next((k for k in sheets if k.strip().lower() == "general"), None)
    if general_key is None:
        general_key = next(iter(sheets))

    meta = {"currency": "AUD"}
    freight_amount = 0.0

    if summary_key:
        df_sum = sheets[summary_key]
        for _, row in df_sum.iterrows():
            row_vals = [str(v).strip() for v in row]
            row_str  = " ".join(row_vals).lower()
            if "quote_number" not in meta:
                for val in row_vals:
                    cleaned = val.replace("-", "").replace(" ", "")
                    if "-" in val and cleaned.isdigit() and 6 <= len(cleaned) <= 12:
                        meta["quote_number"] = val
                        break
            if "expiration date" in row_str or "expiry" in row_str:
                for i, val in enumerate(row_vals):
                    if "expiration" in val.lower() or "expiry" in val.lower():
                        for v in row_vals[i+1:]:
                            if "/" in v or (len(v) >= 8 and "-" in v):
                                meta["expiry"] = v
                                break
                        break
            if "prepared by" in row_str:
                for i, val in enumerate(row_vals):
                    if "prepared by" in val.lower() and i + 1 < len(row_vals) and row_vals[i+1]:
                        meta["prepared_by"] = row_vals[i+1]
                        break
            if "end user" in row_str:
                for i, val in enumerate(row_vals):
                    if "end user" in val.lower():
                        for v in row_vals[i+1:]:
                            if v:
                                meta["end_user"] = v
                                break
                        break

    df_raw = sheets[general_key]

    for _, row in df_raw.iterrows():
        row_vals = [str(v).strip() for v in row]
        row_str  = " ".join(row_vals).lower()
        if "quote_number" not in meta:
            for val in row_vals:
                cleaned = val.replace("-", "").replace(" ", "")
                if "-" in val and cleaned.isdigit() and 6 <= len(cleaned) <= 12:
                    meta["quote_number"] = val
                    break
        if "expiry" not in meta and ("expiration" in row_str or "expiry" in row_str):
            for val in row_vals:
                if "/" in val and len(val) >= 8:
                    meta["expiry"] = val
                    break
        if "freight charge" in row_str and freight_amount == 0.0:
            for val in row_vals:
                amt = extract_amount(val)
                if amt > 0:
                    freight_amount = amt
                    break

    header_idx = None
    col_map    = {}
    for i, row in df_raw.iterrows():
        rs = " ".join(str(v).strip().lower() for v in row)
        if "line no." in rs or ("part no." in rs and "qty" in rs):
            header_idx = i
            for col_idx, cell in enumerate(row):
                label = str(cell).strip().lower()
                if "long description" in label or label == "description":
                    col_map["description"] = col_idx
                elif "customer part" in label:
                    col_map.setdefault("description_fallback", col_idx)
                elif label in ("qty", "quantity"):
                    col_map["qty"] = col_idx
                elif "unit price" in label or "unit cost" in label:
                    col_map["unit_cost"] = col_idx
                elif "ext. price" in label or "ext price" in label or "extended price" in label:
                    col_map["total_cost"] = col_idx
                elif "part no" in label and "customer" not in label:
                    col_map["sku"] = col_idx
            break

    desc_col      = col_map.get("description", col_map.get("description_fallback", 4))
    qty_col       = col_map.get("qty",       2)
    sku_col       = col_map.get("sku",       1)
    unit_cost_col = col_map.get("unit_cost", 7)
    total_cost_col= col_map.get("total_cost",8)

    items = []
    if header_idx is not None:
        line_counter = 1
        for i in range(header_idx + 1, len(df_raw)):
            row      = df_raw.iloc[i]
            row_vals = [str(v).strip() for v in row]
            joined   = " ".join(row_vals).lower()
            if any(x in joined for x in ["subtotal", "general total", "freight", "gst:", "total:"]):
                break
            try:
                int(float(row_vals[0]))
            except (ValueError, TypeError):
                continue
            try:
                desc_val = row_vals[desc_col] if desc_col < len(row_vals) else ""
                if not desc_val and "description_fallback" in col_map:
                    fb = col_map["description_fallback"]
                    desc_val = row_vals[fb] if fb < len(row_vals) else ""
                items.append({
                    "#":           line_counter,
                    "SKU":         row_vals[sku_col]       if sku_col       < len(row_vals) else "",
                    "Description": desc_val,
                    "Qty":         int(float(row_vals[qty_col]))                             if qty_col < len(row_vals) else 0,
                    "Unit Cost":   float(row_vals[unit_cost_col].replace(",", ""))           if unit_cost_col < len(row_vals) else 0.0,
                    "Total Cost":  float(row_vals[total_cost_col].replace(",", ""))          if total_cost_col < len(row_vals) else 0.0,
                })
                line_counter += 1
            except (ValueError, TypeError, IndexError):
                continue

    if freight_amount > 0:
        items.append({
            "#":           len(items) + 1,
            "SKU":         "FREIGHT",
            "Description": "Freight Charge",
            "Qty":         1,
            "Unit Cost":   freight_amount,
            "Total Cost":  freight_amount,
        })

    return meta, pd.DataFrame(items)


# ── Shared helpers ─────────────────────────────────────────────────────────────
def apply_margin(items: pd.DataFrame, margin_pct: float) -> pd.DataFrame:
    m  = margin_pct / 100.0
    df = items.copy()
    df["Unit Price"] = df["Unit Cost"] / (1 - m)
    df["Total"]      = df["Unit Price"] * df["Qty"]
    return df


def fmt(val) -> str:
    try:
        return f"$ {float(val):,.2f}"
    except (ValueError, TypeError):
        return str(val) if val != "" else ""


def add_totals_cost(df: pd.DataFrame) -> pd.DataFrame:
    totals = {"#": "", "SKU": "", "Description": "TOTAL",
               "Qty": "", "Unit Cost": "", "Total Cost": df["Total Cost"].sum()}
    return pd.concat([df, pd.DataFrame([totals])], ignore_index=True)


def add_totals_sell(df: pd.DataFrame) -> pd.DataFrame:
    totals = {"#": "", "SKU": "", "Description": "TOTAL",
               "Qty": "", "Unit Price": "", "Total": df["Total"].sum()}
    return pd.concat([df, pd.DataFrame([totals])], ignore_index=True)


RIGHT_ALIGN_COLS = {"Qty", "Unit Cost", "Total Cost", "Unit Price", "Total"}


def render_html_table(df: pd.DataFrame, money_cols: list) -> str:
    def header_align(col):
        return "center" if col in RIGHT_ALIGN_COLS else "left"

    styles = """
    <style>
      .madit-table { width:100%; border-collapse:collapse; font-size:0.78rem;
                     font-family:'Inter','Segoe UI',sans-serif; }
      .madit-table thead tr { background-color:#1a2a3a; color:#fff; }
      .madit-table thead th { padding:8px 12px; font-weight:600;
                               letter-spacing:.03em; white-space:nowrap; }
      .madit-table tbody tr { border-bottom:1px solid #e8e8e8; }
      .madit-table tbody tr:nth-child(even) { background:#f7f9fb; }
      .madit-table tbody tr:hover { background:#eef3f8; }
      .madit-table tbody td { padding:6px 12px; color:#2c3e50; vertical-align:top; }
      .madit-table .left  { text-align:left; }
      .madit-table .right { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
      .madit-table .total-row td { font-weight:700; background:#e8f0f7 !important;
                                    border-top:2px solid #1a2a3a; color:#0f1923; }
    </style>
    """

    header = "<thead><tr>" + "".join(
        f'<th style="text-align:{header_align(col)}">{col}</th>'
        for col in df.columns
    ) + "</tr></thead>"

    rows_html = ""
    for _, row in df.iterrows():
        is_total  = str(row.get("Description", "")).strip().upper() == "TOTAL"
        row_class = ' class="total-row"' if is_total else ""
        cells = ""
        for col in df.columns:
            val       = row[col]
            css_class = "right" if col in RIGHT_ALIGN_COLS else "left"
            display   = fmt(val) if col in money_cols else (_html.escape(str(val)) if val != "" else "")
            cells    += f'<td class="{css_class}">{display}</td>'
        rows_html += f"<tr{row_class}>{cells}</tr>"

    return styles + f'<table class="madit-table">{header}<tbody>{rows_html}</tbody></table>'


def render_summary_table(summary: pd.DataFrame) -> str:
    styles = """
    <style>
      .summary-table { width:100%; border-collapse:collapse; font-size:0.82rem;
                       font-family:'Inter','Segoe UI',sans-serif; }
      .summary-table thead tr { background:#1a2a3a; color:#fff; }
      .summary-table thead th { padding:9px 16px; text-align:center;
                                  font-weight:600; letter-spacing:.03em; }
      .summary-table thead th:first-child { text-align:left; }
      .summary-table tbody tr { border-bottom:1px solid #e0e0e0; }
      .summary-table tbody tr:last-child { font-weight:700; background:#e8f0f7;
                                            border-top:2px solid #1a2a3a; }
      .summary-table tbody td { padding:8px 16px; color:#2c3e50;
                                  font-variant-numeric:tabular-nums; }
      .summary-table tbody td:first-child { text-align:left; }
      .summary-table tbody td:not(:first-child) { text-align:right; white-space:nowrap; }
    </style>
    """
    header = (
        "<thead><tr>"
        + "".join(f"<th>{col}</th>" for col in summary.columns)
        + "</tr></thead>"
    )
    rows_html = "".join(
        "<tr>" + "".join(f"<td>{row[col]}</td>" for col in summary.columns) + "</tr>"
        for _, row in summary.iterrows()
    )
    return styles + f'<table class="summary-table">{header}<tbody>{rows_html}</tbody></table>'


# ── Misc helpers ────────────────────────────────────────────────────────────────
def _mime_for_filename(filename: str) -> str:
    """Devuelve el mime type correcto según la extensión real del archivo
    (para que los botones de descarga sirvan tanto .xlsx como .xls)."""
    if filename.lower().endswith(".xls") and not filename.lower().endswith(".xlsx"):
        return "application/vnd.ms-excel"
    return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ── Repository / navigation state helpers ───────────────────────────────────────
NEW_QUOTE_STATE_KEYS = [
    "items_saved", "quote_file_id", "meta", "distributor", "edit_mode",
    "edit_counter", "items_snapshot", "quote_saved_record", "loaded_record_id",
    "original_excel_bytes", "original_excel_name",
]

# Selectbox keys that must be dropped whenever the underlying client/contact
# changes, so they re-initialize from `index=` instead of a stale value.
CLIENT_FORM_WIDGET_KEYS = ["company_select", "contact_select"]

NEW_COMPANY_LABEL = "➕ New company..."
NEW_CONTACT_LABEL = "➕ New contact..."


def _reset_new_quote_flow():
    for key in NEW_QUOTE_STATE_KEYS + CLIENT_FORM_WIDGET_KEYS:
        st.session_state.pop(key, None)
    st.session_state["quote_client"]         = ""
    st.session_state["quote_contact"]        = ""
    st.session_state["quote_contact_title"]  = ""
    st.session_state["quote_contact_mobile"] = ""
    st.session_state["quote_email"]          = ""
    st.session_state["quote_title"]          = ""
    st.session_state["quote_date_obj"]       = datetime.today().date()
    st.session_state["margin_pct"]           = 10.0


def _load_saved_quote(record: dict):
    from tools import quotes_repo

    with st.spinner("Loading saved quote..."):
        # Se pasa el registro completo (no solo el id) porque el detalle y el
        # excel ahora pueden vivir en una carpeta por cliente; el registro
        # trae 'detail_path'/'excel_path' (o cae a la ruta plana antigua si
        # es una quote guardada antes de esta reorganización).
        detail      = quotes_repo.load_quote_detail(record)
        excel_bytes = quotes_repo.download_quote_excel(detail)

    for key in CLIENT_FORM_WIDGET_KEYS:
        st.session_state.pop(key, None)

    items = pd.DataFrame(detail["items"])

    try:
        date_obj = datetime.strptime(detail["date"], "%d/%m/%Y").date()
    except (ValueError, TypeError):
        date_obj = datetime.today().date()

    st.session_state["items_saved"]          = items
    st.session_state["meta"]                 = detail["meta"]
    st.session_state["distributor"]          = detail["distributor"]
    st.session_state["margin_pct"]           = detail.get("margin_pct", 10.0)
    st.session_state["edit_mode"]            = False
    st.session_state["edit_counter"]         = 0
    st.session_state["quote_client"]         = detail.get("client", "")
    st.session_state["quote_contact"]        = detail.get("contact", "")
    st.session_state["quote_contact_title"]  = detail.get("contact_title", "")
    st.session_state["quote_contact_mobile"] = detail.get("contact_mobile", "")
    st.session_state["quote_email"]          = detail.get("email", "")
    st.session_state["quote_title"]          = detail.get("title", "")
    st.session_state["quote_date_obj"]       = date_obj
    st.session_state["loaded_record_id"]     = detail["id"]
    st.session_state["original_excel_bytes"] = excel_bytes
    st.session_state["original_excel_name"]  = detail["filename"]
    st.session_state["quote_saved_record"]   = record


# ── Quote History ───────────────────────────────────────────────────────────────
def _show_history():
    from tools import quotes_repo

    st.markdown("### 📚 Quote History")

    with st.spinner("Loading history..."):
        try:
            quotes = quotes_repo.load_quotes()
        except Exception as e:
            st.error(f"❌ Error loading history: {e}")
            return

    if not quotes:
        st.info("No saved quotes yet.")
        return

    # Cache de bytes de Excel ya descargados en esta sesión, para no volver
    # a pedirlos a GitHub en cada rerender (patrón de 2 pasos requerido por
    # st.download_button, que necesita los bytes disponibles de antemano).
    if "quote_file_cache" not in st.session_state:
        st.session_state["quote_file_cache"] = {}

    clients = sorted(set(q.get("client", "—") for q in quotes))
    col_f, _ = st.columns([1, 3])
    with col_f:
        client_filter = st.selectbox("Filter by client", ["All"] + clients)

    filtered = quotes if client_filter == "All" else [q for q in quotes if q.get("client") == client_filter]

    grouped: dict[str, list] = {}
    for q in filtered:
        grouped.setdefault(q.get("client", "—"), []).append(q)

    for client, recs in sorted(grouped.items(), key=lambda kv: kv[0].lower()):

        def _date_key(r):
            try:
                return datetime.strptime(r.get("date", ""), "%d/%m/%Y")
            except (ValueError, TypeError):
                return datetime.min

        recs_sorted = sorted(recs, key=_date_key, reverse=True)

        with st.expander(f"🏢 {client}  ·  {len(recs_sorted)} quote(s)", expanded=(client_filter != "All")):
            hc1, hc2, hc3, hc4, hc5, hc6 = st.columns([2.2, 1.3, 1.8, 1.3, 0.9, 1.3])
            hc1.markdown("**Title**")
            hc2.markdown("**Date**")
            hc3.markdown("**Quote #**")
            hc4.markdown("**Total (Sell)**")
            hc5.markdown("")
            hc6.markdown("")

            for rec in recs_sorted:
                c1, c2, c3, c4, c5, c6 = st.columns([2.2, 1.3, 1.8, 1.3, 0.9, 1.3])
                c1.write(rec.get("title", "—") or "—")
                c2.write(rec.get("date", "—"))
                c3.write(f"#{rec.get('quote_number', '—')}  ({rec.get('distributor', '—')})")
                c4.write(fmt(rec.get("sell_total", 0)))
                with c5:
                    if st.button("Open", key=f"open_{rec['id']}", use_container_width=True):
                        _load_saved_quote(rec)
                        st.session_state["quote_view"] = "new"
                        st.rerun()
                with c6:
                    cache    = st.session_state["quote_file_cache"]
                    filename = rec.get("filename", "")
                    if not filename:
                        st.caption("—")
                    elif rec["id"] in cache:
                        st.download_button(
                            "⬇️ Save",
                            data=cache[rec["id"]],
                            file_name=filename,
                            mime=_mime_for_filename(filename),
                            key=f"dl_{rec['id']}",
                            use_container_width=True,
                        )
                    else:
                        if st.button("⬇️ File", key=f"prep_{rec['id']}", use_container_width=True):
                            try:
                                with st.spinner("Fetching file..."):
                                    # Se pasa el registro completo (rec), no solo el
                                    # filename, porque el archivo ahora puede vivir
                                    # dentro de la carpeta del cliente.
                                    file_bytes = quotes_repo.download_quote_excel(rec)
                                cache[rec["id"]] = file_bytes
                                st.rerun()
                            except Exception as e:
                                st.error(f"❌ Could not download: {e}")


# ── New Quote / View Saved Quote ────────────────────────────────────────────────
def _show_new_quote():
    from tools import quotes_repo

    loaded_id = st.session_state.get("loaded_record_id")
    if loaded_id:
        st.info(
            f"📂 Viewing saved quote: **{st.session_state.get('quote_title', '')}** "
            f"— {st.session_state.get('quote_client', '')}"
        )

    # ── Step 1: date + client/proposal form ──────────────────────────────────
    st.markdown("### 📝 Quote Details")

    if "clients_db" not in st.session_state:
        try:
            st.session_state["clients_db"] = quotes_repo.load_clients_db()
        except Exception as e:
            st.session_state["clients_db"] = {}
            st.warning(f"Could not load clients database: {e}")

    clients_db = st.session_state["clients_db"]

    with st.container(border=True):
        hcol1, hcol2 = st.columns([5, 1.3])
        with hcol1:
            st.markdown(
                "<div style='font-size:0.95rem;font-weight:700;color:#1a2a3a;"
                "letter-spacing:.02em;margin-top:6px;'>🏢 CLIENT &amp; CONTACT</div>",
                unsafe_allow_html=True,
            )
        with hcol2:
            if st.button("🔄 Refresh clients", key="refresh_clients_db", use_container_width=True):
                st.session_state.pop("clients_db", None)
                st.rerun()

        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

        # ── Company — full width ─────────────────────────────────────────────
        company_options = sorted(clients_db.keys()) + [NEW_COMPANY_LABEL]
        current_client   = st.session_state.get("quote_client", "")
        default_idx = company_options.index(current_client) if current_client in clients_db else len(company_options) - 1

        def _on_company_change():
            choice = st.session_state["company_select"]
            st.session_state.pop("contact_select", None)
            if choice != NEW_COMPANY_LABEL:
                st.session_state["quote_client"]         = choice
                st.session_state["quote_contact"]        = ""
                st.session_state["quote_email"]          = ""
                st.session_state["quote_contact_title"]  = ""
                st.session_state["quote_contact_mobile"] = ""
            else:
                st.session_state["quote_client"] = ""

        st.selectbox("🏢 Company", company_options, index=default_idx, key="company_select", on_change=_on_company_change)

        if st.session_state["company_select"] == NEW_COMPANY_LABEL:
            st.text_input("New company name", key="quote_client")

        st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)

        # ── Contact Name / Contact Title ─────────────────────────────────────
        cc1, cc2 = st.columns(2)
        with cc1:
            contacts_list   = clients_db.get(st.session_state.get("quote_client", ""), [])
            contact_names   = [c.get("contact", "") for c in contacts_list if c.get("contact")]
            contact_options = contact_names + [NEW_CONTACT_LABEL]
            current_contact = st.session_state.get("quote_contact", "")
            default_c_idx = contact_options.index(current_contact) if current_contact in contact_names else len(contact_options) - 1

            def _on_contact_change():
                choice = st.session_state["contact_select"]
                if choice != NEW_CONTACT_LABEL:
                    match = next((c for c in contacts_list if c.get("contact") == choice), None)
                    st.session_state["quote_contact"]        = choice
                    st.session_state["quote_email"]          = match.get("email", "")  if match else ""
                    st.session_state["quote_contact_title"]  = match.get("title", "")  if match else ""
                    st.session_state["quote_contact_mobile"] = match.get("mobile", "") if match else ""
                else:
                    st.session_state["quote_contact"]        = ""
                    st.session_state["quote_email"]          = ""
                    st.session_state["quote_contact_title"]  = ""
                    st.session_state["quote_contact_mobile"] = ""

            st.selectbox("👤 Contact Name", contact_options, index=default_c_idx, key="contact_select", on_change=_on_contact_change)

            if st.session_state["contact_select"] == NEW_CONTACT_LABEL:
                st.text_input("New contact name", key="quote_contact")

        with cc2:
            st.text_input("💼 Contact Title", key="quote_contact_title")

        # ── Mobile Phone / Email ─────────────────────────────────────────────
        cc3, cc4 = st.columns(2)
        with cc3:
            st.text_input("📱 Mobile Phone", key="quote_contact_mobile")
        with cc4:
            st.text_input("✉️ Email", key="quote_email")

    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    pc1, pc2 = st.columns(2)
    with pc1:
        st.text_input("📌 Proposal Title", key="quote_title")
    with pc2:
        st.date_input("📅 Date", key="quote_date_obj", format="DD/MM/YYYY")

    st.divider()

    # ── Step 2: upload distributor quote (skipped if loaded from repo) ───────
    if loaded_id:
        cap_col, dl_col = st.columns([4, 1])
        with cap_col:
            st.caption(f"📎 Original Excel: {st.session_state.get('original_excel_name', '')}")
        with dl_col:
            original_bytes = st.session_state.get("original_excel_bytes")
            original_name  = st.session_state.get("original_excel_name", "quote.xlsx")
            if original_bytes:
                st.download_button(
                    "⬇️ Download",
                    data=original_bytes,
                    file_name=original_name,
                    mime=_mime_for_filename(original_name),
                    key="dl_loaded_original",
                    use_container_width=True,
                )
    else:
        uploaded = st.file_uploader(
            "Upload distributor quote (.xlsx or .xls)",
            type=["xlsx", "xls"],
            key="quote_upload",
        )

        if uploaded is None:
            st.info("Upload a NEXTGEN or TECHDATA Excel quote to get started.")
            for key in ["items_saved", "quote_file_id"]:
                st.session_state.pop(key, None)
            return

        file_id = (uploaded.name, uploaded.size)
        if st.session_state.get("quote_file_id") != file_id:
            with st.spinner("Processing quote..."):
                sheets      = read_quote_file(uploaded, uploaded.name)
                distributor = detect_distributor(sheets)
                if distributor == "NEXTGEN":
                    meta, items = parse_nextgen(sheets)
                else:
                    meta, items = parse_techdata(sheets)

            if items.empty:
                st.error("No line items found — is this a valid quote?")
                return

            uploaded.seek(0)
            # Se guarda el archivo original tal cual (bytes + nombre) para
            # poder subirlo íntegro a la carpeta del cliente en el repo
            # privado al momento de "Save Quote", y así poder reabrirlo /
            # descargarlo más adelante.
            st.session_state["original_excel_bytes"] = uploaded.read()
            st.session_state["original_excel_name"]  = uploaded.name
            st.session_state["quote_file_id"]  = file_id
            st.session_state["distributor"]    = distributor
            st.session_state["meta"]           = meta
            st.session_state["items_saved"]    = items.copy()
            st.session_state["edit_mode"]      = False
            st.session_state["edit_counter"]   = 0
            st.session_state["quote_saved_record"] = None

    if "items_saved" not in st.session_state:
        st.error("No quote data loaded.")
        return

    distributor = st.session_state["distributor"]
    meta        = st.session_state["meta"]
    items       = st.session_state["items_saved"]
    edit_mode   = st.session_state.get("edit_mode", False)

    # ── Badge ──────────────────────────────────────────────────────────────────
    badge_color = "#0077b6" if distributor == "TECHDATA" else "#2d6a4f"
    st.markdown(
        f'<span style="background:{badge_color};color:#fff;padding:3px 10px;'
        f'border-radius:12px;font-size:0.75rem;font-weight:600;">'
        f'🏷 {distributor}</span>',
        unsafe_allow_html=True,
    )
    st.markdown("")

    # ── Quote info ─────────────────────────────────────────────────────────────
    st.markdown("### 📄 Quote Information")
    col1, col2, col3 = st.columns(3)
    col1.metric("Quote #",  meta.get("quote_number", "—"))
    col2.metric("Expiry",   meta.get("expiry",        "—"))
    col3.metric("Currency", meta.get("currency",      "AUD"))

    with st.expander("More details"):
        st.write(f"**Client:** {st.session_state.get('quote_client', '—') or '—'}")
        st.write(f"**Contact:** {st.session_state.get('quote_contact', '—') or '—'}")
        st.write(f"**Title:** {st.session_state.get('quote_contact_title', '—') or '—'}")
        st.write(f"**Mobile:** {st.session_state.get('quote_contact_mobile', '—') or '—'}")
        st.write(f"**Email:** {st.session_state.get('quote_email', '—') or '—'}")
        st.write(f"**Proposal title:** {st.session_state.get('quote_title', '—') or '—'}")
        st.write(f"**End User:** {meta.get('end_user', '—')}")
        if distributor == "NEXTGEN":
            st.write(f"**Description:** {meta.get('description', '—')}")
            st.write(f"**Reseller:** {meta.get('reseller', '—')}")
        else:
            st.write(f"**Prepared By:** {meta.get('prepared_by', '—')}")

    st.divider()

    # ── Margin input (always visible) ──────────────────────────────────────────
    if "margin_pct" not in st.session_state:
        st.session_state["margin_pct"] = 10.0

    # ── Distributor cost table ─────────────────────────────────────────────────
    col_title, col_btn = st.columns([6, 1])
    with col_title:
        st.markdown("### 🛒 Distributor Cost")

    editor_key = f"cost_editor_widget_{st.session_state.get('edit_counter', 0)}"

    if not edit_mode:
        # ── READ-ONLY mode ─────────────────────────────────────────────────────
        with col_btn:
            st.markdown("<div style='padding-top:28px'>", unsafe_allow_html=True)
            if st.button("✏️ Edit", key="btn_edit", use_container_width=True):
                st.session_state["items_snapshot"] = st.session_state["items_saved"].copy()
                st.session_state["edit_mode"]    = True
                st.session_state["edit_counter"] = st.session_state.get("edit_counter", 0) + 1
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)

        st.markdown(
            render_html_table(add_totals_cost(items), ["Unit Cost", "Total Cost"]),
            unsafe_allow_html=True,
        )

    else:
        # ── EDIT mode ──────────────────────────────────────────────────────────
        with col_btn:
            st.markdown("<div style='padding-top:28px'>", unsafe_allow_html=True)
            save_clicked = st.button("💾 Save", key="btn_save", type="primary", use_container_width=True)
            st.markdown("</div>", unsafe_allow_html=True)

        cancel_col, _ = st.columns([1, 5])
        with cancel_col:
            cancel_clicked = st.button("✖ Cancel", key="btn_cancel", use_container_width=True)

        st.caption("✏️ Edit **Unit Cost**, **Qty** or **Description** inline · Select rows with the checkbox and press **Delete** to remove them")

        snapshot = st.session_state.get("items_snapshot", items).copy()

        edited_df = st.data_editor(
            snapshot,
            key=editor_key,
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            column_config={
                "#": st.column_config.NumberColumn("#", width="small", disabled=True),
                "SKU": st.column_config.TextColumn("SKU", width="medium"),
                "Description": st.column_config.TextColumn("Description", width="large"),
                "Qty": st.column_config.NumberColumn("Qty", width="small", min_value=0, step=1, format="%d"),
                "Unit Cost": st.column_config.NumberColumn("Unit Cost", width="medium", min_value=0.0, step=0.01, format="$ %.2f"),
                "Total Cost": st.column_config.NumberColumn("Total Cost", width="medium", disabled=True, format="$ %.2f"),
            },
        )

        if save_clicked:
            committed = edited_df.copy().reset_index(drop=True)
            committed["Unit Cost"]  = pd.to_numeric(committed["Unit Cost"],  errors="coerce").fillna(0.0)
            committed["Qty"]        = pd.to_numeric(committed["Qty"],        errors="coerce").fillna(0).astype(int)
            committed["Total Cost"] = committed["Unit Cost"] * committed["Qty"]
            st.session_state["items_saved"] = committed
            st.session_state["edit_mode"]   = False
            st.session_state.pop("items_snapshot", None)
            st.session_state.pop(editor_key, None)
            st.rerun()

        if cancel_clicked:
            st.session_state["edit_mode"] = False
            st.session_state.pop("items_snapshot", None)
            st.session_state.pop(editor_key, None)
            st.rerun()

    st.divider()

    # ── Sell price table ───────────────────────────────────────────────────────
    st.markdown("### 💰 Sell Price with Margin")

    df_margin = apply_margin(items, st.session_state["margin_pct"])
    sell_cols = ["#", "SKU", "Description", "Qty", "Unit Price", "Total"]
    st.markdown(
        render_html_table(
            add_totals_sell(df_margin[sell_cols].copy()),
            ["Unit Price", "Total"],
        ),
        unsafe_allow_html=True,
    )

    st.divider()

    # ── Summary ────────────────────────────────────────────────────────────────
    st.markdown("### 📊 Summary")

    _, col_input, _ = st.columns([1, 1, 1])
    with col_input:
        st.number_input(
            "Margin (%)",
            min_value=0.0,
            max_value=99.0,
            value=st.session_state["margin_pct"],
            step=0.5,
            format="%.1f",
            help="Formula: Sell Price = Cost / (1 − Margin)",
            key="margin_pct",
        )

    margin_pct   = st.session_state["margin_pct"]
    df_margin    = apply_margin(items, margin_pct)

    cost_total   = items["Total Cost"].sum()
    cost_gst     = cost_total * 0.10
    cost_inc_gst = cost_total + cost_gst
    sell_total   = df_margin["Total"].sum()
    sell_gst     = sell_total * 0.10
    sell_inc_gst = sell_total + sell_gst

    summary = pd.DataFrame([
        {"": "Subtotal (ex. GST)", "Cost": fmt(cost_total),   "Sell Price": fmt(sell_total),
         "Difference": fmt(sell_total   - cost_total)},
        {"": "GST (10%)",          "Cost": fmt(cost_gst),     "Sell Price": fmt(sell_gst),
         "Difference": fmt(sell_gst     - cost_gst)},
        {"": "Total (inc. GST)",   "Cost": fmt(cost_inc_gst), "Sell Price": fmt(sell_inc_gst),
         "Difference": fmt(sell_inc_gst - cost_inc_gst)},
    ])

    _, col_mid, _ = st.columns([1, 2, 1])
    with col_mid:
        st.markdown(render_summary_table(summary), unsafe_allow_html=True)

    # ── Save to Repository ──────────────────────────────────────────────────────
    st.divider()
    st.markdown("### 💾 Save to Repository")

    can_save = bool(st.session_state.get("quote_client", "").strip()) and bool(st.session_state.get("quote_title", "").strip())
    if not can_save:
        st.warning("Fill in at least **Company** and **Proposal title** to be able to save.")

    if st.button("💾 Save Quote", type="primary", disabled=not can_save):
        with st.spinner("Saving to repository..."):
            try:
                record = quotes_repo.save_quote(
                    client=st.session_state["quote_client"],
                    contact=st.session_state["quote_contact"],
                    contact_title=st.session_state.get("quote_contact_title", ""),
                    contact_mobile=st.session_state.get("quote_contact_mobile", ""),
                    email=st.session_state["quote_email"],
                    title=st.session_state["quote_title"],
                    date=st.session_state["quote_date_obj"].strftime("%d/%m/%Y"),
                    meta=meta,
                    items=items,
                    margin_pct=margin_pct,
                    distributor=distributor,
                    file_bytes=st.session_state.get("original_excel_bytes"),
                    original_filename=st.session_state.get("original_excel_name", ""),
                    record_id=st.session_state.get("loaded_record_id"),
                )
            except Exception as e:
                st.error(f"❌ Error saving to repository: {e}")
                record = None

        if record:
            st.session_state["quote_saved_record"] = record
            st.session_state["loaded_record_id"]   = record["id"]

            try:
                quotes_repo.upsert_client_contact(
                    client=st.session_state["quote_client"],
                    contact=st.session_state["quote_contact"],
                    email=st.session_state["quote_email"],
                    title=st.session_state.get("quote_contact_title", ""),
                    mobile=st.session_state.get("quote_contact_mobile", ""),
                )
                # keep the local cache in sync so it's available immediately
                # without another API round trip
                clients_db = st.session_state.get("clients_db", {})
                contacts = clients_db.setdefault(st.session_state["quote_client"], [])
                names = [c.get("contact", "") for c in contacts]
                if st.session_state["quote_contact"] and st.session_state["quote_contact"] not in names:
                    contacts.append({
                        "contact": st.session_state["quote_contact"],
                        "email":   st.session_state["quote_email"],
                        "title":   st.session_state.get("quote_contact_title", ""),
                        "mobile":  st.session_state.get("quote_contact_mobile", ""),
                    })
                elif st.session_state["quote_contact"]:
                    for c in contacts:
                        if c.get("contact") == st.session_state["quote_contact"]:
                            c["email"]  = st.session_state["quote_email"]
                            c["title"]  = st.session_state.get("quote_contact_title", "")
                            c["mobile"] = st.session_state.get("quote_contact_mobile", "")
                            break
            except Exception as e:
                st.warning(f"Quote saved, but could not update the clients database: {e}")

            st.success(f"✅ Quote saved — Quote #{record.get('quote_number', '—')}")

    # ── Xero (only available after saving) ──────────────────────────────────────
    st.divider()
    st.markdown("### 🔗 Send to Xero")

    if not st.session_state.get("quote_saved_record"):
        st.info("Save the quote to the repository before sending it to Xero.")
    else:
        from integrations import xero as xero_integration

        if xero_integration.is_connected():
            st.success("✅ Xero connected")
            if st.button("📤 Send to Xero as Draft Quote", type="primary"):
                with st.spinner("Sending to Xero..."):
                    try:
                        result    = xero_integration.create_draft_quote(meta, items, margin_pct)
                        quote_num = result.get("QuoteNumber", "")
                        quote_id  = result.get("QuoteID", "")
                        st.success(f"✅ Draft quote created in Xero! Quote #{quote_num} — ID: {quote_id}")
                    except Exception as e:
                        st.error(f"❌ Error sending to Xero: {e}")
                        if hasattr(e, "response") and e.response is not None:
                            st.json(e.response.json())
        else:
            try:
                auth_url = xero_integration.get_auth_url()
                col_connect, col_verify, _ = st.columns([2, 2, 3])
                with col_connect:
                    st.markdown(
                        f'<a href="{auth_url}" target="_blank" rel="noopener noreferrer" '
                        f'style="display:inline-block;background:#1a6fe8;color:#fff;'
                        f'padding:10px 18px;border-radius:8px;text-decoration:none;'
                        f'font-size:0.88rem;font-weight:500;">'
                        f'🔗 Connect to Xero</a>',
                        unsafe_allow_html=True,
                    )
                with col_verify:
                    if st.button("🔄 I've connected — verify", type="secondary"):
                        if TOKEN_FILE.exists():
                            tokens = json.loads(TOKEN_FILE.read_text())
                            st.session_state["xero_tokens"] = tokens
                            TOKEN_FILE.unlink()
                            st.rerun()
                        else:
                            st.warning("Token not found yet — wait a few seconds and try again.")
            except KeyError:
                st.warning("⚠️ Xero credentials not configured. Add `[xero]` to your Streamlit secrets.")


# ── Main page ──────────────────────────────────────────────────────────────────
def show():
    st.title("📋 QUOTES")

    if "quote_view" not in st.session_state:
        st.session_state["quote_view"] = "new"
        _reset_new_quote_flow()

    nav1, nav2, _ = st.columns([1.3, 1.3, 3])
    with nav1:
        if st.button("🆕 New Quote", use_container_width=True):
            _reset_new_quote_flow()
            st.session_state["quote_view"] = "new"
            st.rerun()
    with nav2:
        if st.button("📚 Quote History", use_container_width=True):
            st.session_state["quote_view"] = "history"
            st.rerun()

    st.divider()

    if st.session_state["quote_view"] == "history":
        _show_history()
    else:
        _show_new_quote()
