"""MADIT | Transceivers tool (Flexoptix API).

Page for the MADIT internal portal. Exposes show(), like the other tools.

Tabs:
  1. Inventory - where each module is (vendor, switch, port, location...), saved in the
                private GitHub repo by inventory_db.py. Opens by default.
  2. Product  - lookup by part number with specifications and compatibilities.
  3. Serials  - lookup by serial number with Flexoptix's Serial Number API: model,
                key specifications, delivery and order; units by model.
  4. Catalog  - full Flexoptix catalogue in USD: list price, my price (self-configure),
                sell price (my price + margin), filters, CSV export. Margin and
                connection test are shown only here.

Credentials: Streamlit secrets, section [flexoptix] (and [github] for the inventory,
the same one the Clients tool uses):

    [flexoptix]
    user = "your_api_user_email"
    password = "your_api_user_password"

Catalogue cache is written to the system temp folder (never into the repository),
so prices are not committed to the public repo.
"""
from __future__ import annotations

import json
import re
import tempfile
import time
from html import unescape
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st

from tools.transceivers import inventory_db as inv_db

API_V1 = "https://www.flexoptix.net/rest/V1"
API_V2 = "https://www.flexoptix.net/rest/V2"
CURRENCY = "USD"           # USD only for now
TIMEOUT = 60
PAGE_LIMIT = 500           # the docs recommend not going above 500
MAX_PAGES = 200            # safety brake
TOKEN_TTL = 50 * 60        # token lasts 1 hour; refresh earlier
CATALOG_TTL = 6 * 3600     # on-disk catalogue cache
CACHE_DIR = Path(tempfile.gettempdir()) / "madit_flexoptix"

CATALOG_COLS = [
    "sku", "name", "type", "parent_sku",
    "price", "self_configure_price", "list_price", "stock", "url", "image",
]
NUMERIC_COLS = ["price", "self_configure_price", "list_price", "stock"]


class ApiError(Exception):
    """Error talking to the Flexoptix API."""


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _creds() -> tuple[str, str]:
    try:
        cfg = st.secrets["flexoptix"]
        return str(cfg["user"]).strip(), str(cfg["password"])
    except Exception:
        return "", ""


def unescape_all(value):
    """The API returns HTML-escaped strings; unescape the whole structure."""
    if isinstance(value, str):
        return unescape(value)
    if isinstance(value, list):
        return [unescape_all(v) for v in value]
    if isinstance(value, dict):
        return {k: unescape_all(v) for k, v in value.items()}
    return value


def flatten(value, prefix: str = "") -> dict:
    """Flatten nested dicts into dotted keys; lists are stored as JSON strings."""
    out: dict = {}
    if isinstance(value, dict):
        for k, v in value.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            out.update(flatten(v, key))
    elif isinstance(value, list):
        out[prefix] = json.dumps(value, ensure_ascii=False)
    else:
        out[prefix] = "" if value is None else value
    return out


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def item_prices(item: dict, margin: float) -> tuple:
    """(list price, my price, sell price) for one API product.

    - my price   = self_configure_price, or the normal price if there is none
    - list price = list_price, or the normal price (the API only sends list_price
                   when it differs from the normal price)
    - sell price = my price + margin
    """
    def num(v):
        try:
            return None if v is None else float(v)
        except (TypeError, ValueError):
            return None

    price = num(item.get("price"))
    mine = num(item.get("self_configure_price"))
    mine = mine if mine is not None else price
    listed = num(item.get("list_price"))
    listed = listed if listed is not None else price
    sell = round(mine * (1 + margin / 100), 2) if mine is not None else None
    return listed, mine, sell


# --------------------------------------------------------------------------
# Authentication and API calls
# --------------------------------------------------------------------------
def get_token(force: bool = False) -> str:
    user, pwd = _creds()
    if not user or not pwd:
        raise ApiError(
            "Flexoptix credentials not found. Add a [flexoptix] section with "
            "`user` and `password` to the Streamlit secrets."
        )

    ss = st.session_state
    if (
        not force
        and ss.get("fx_token")
        and ss.get("fx_token_user") == user
        and time.time() - ss.get("fx_token_ts", 0) < TOKEN_TTL
    ):
        return ss["fx_token"]

    try:
        r = requests.post(
            f"{API_V1}/integration/customer/token",
            json={"username": user, "password": pwd},
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise ApiError(f"Network error while authenticating: {exc}") from exc

    if r.status_code == 401:
        raise ApiError("Invalid Flexoptix credentials (401).")
    if not r.ok:
        raise ApiError(f"Authentication failed: HTTP {r.status_code}")

    token = r.json()
    if not isinstance(token, str) or not token:
        raise ApiError("Unexpected authentication response.")

    ss["fx_token"] = token
    ss["fx_token_user"] = user
    ss["fx_token_ts"] = time.time()
    return token


def api_get(url: str, params: dict | None = None) -> requests.Response:
    """Authenticated GET; if the token is rejected (401) refresh it and retry once."""
    r = None
    for attempt in (1, 2):
        token = get_token(force=(attempt == 2))
        try:
            r = requests.get(
                url,
                params=params,
                headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            raise ApiError(f"Network error: {exc}") from exc
        if r.status_code == 401 and attempt == 1:
            continue
        break
    return r


# --------------------------------------------------------------------------
# Catalogue
# --------------------------------------------------------------------------
def fetch_catalog(status=None) -> list[dict]:
    items: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        r = api_get(
            f"{API_V2}/flexoptix/products",
            {"page": page, "limit": PAGE_LIMIT, "currency": CURRENCY},
        )
        if not r.ok:
            raise ApiError(f"Catalogue page {page}: HTTP {r.status_code} {r.text[:200]}")
        data = r.json()
        if not data:  # the last page returns an empty array
            break
        items.extend(data)
        if status is not None:
            status.text(f"Page {page} downloaded: {len(items)} products so far")
    return unescape_all(items)


def load_catalog(force: bool = False, status=None):
    path = CACHE_DIR / f"catalog_{CURRENCY}.json"
    if not force and path.exists() and time.time() - path.stat().st_mtime < CATALOG_TTL:
        return json.loads(path.read_text(encoding="utf-8")), path.stat().st_mtime

    items = fetch_catalog(status)
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # no disk cache is not a problem
    return items, time.time()


def tab_catalog(margin: float) -> None:
    c1, c2 = st.columns([1, 4])
    load_clicked = c1.button("Load / refresh catalog", type="primary", key="fx_load")
    force = c2.checkbox("Force a fresh download (ignore the 6-hour cache)", key="fx_force")

    if load_clicked:
        placeholder = st.empty()
        try:
            items, ts = load_catalog(force, placeholder)
            st.session_state["fx_catalog"] = {"items": items, "ts": ts}
        except ApiError as exc:
            st.error(str(exc))
        finally:
            placeholder.empty()

    cat = st.session_state.get("fx_catalog")
    if not cat:
        st.info("Click “Load / refresh catalog” to download the Flexoptix catalogue.")
        return

    st.caption(
        f"Catalogue in {CURRENCY} · downloaded "
        f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(cat['ts']))}"
    )

    df = pd.DataFrame(cat["items"])
    for col in CATALOG_COLS:
        if col not in df.columns:
            df[col] = None
    for col in NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # My price = self-configure price (or the normal price if there is none).
    # List price = list_price (the API omits it when it equals the normal price).
    df["my_price"] = df["self_configure_price"].fillna(df["price"])
    df["list_price"] = df["list_price"].fillna(df["price"])
    df["sell_price"] = (df["my_price"] * (1 + margin / 100)).round(2)

    dups = int(df["sku"].duplicated().sum())
    if dups:
        st.warning(f"{dups} SKUs appear more than once in the API response.")

    # Filters
    f1, f2, f3, f4 = st.columns([3, 1.5, 2.5, 1.5])
    text = f1.text_input("Search (SKU or name)", placeholder="10G SFP+ LR, P.1396.10, 1310...", key="fx_search")
    only_stock = f2.checkbox("In stock only", key="fx_stock")
    types = sorted(str(t) for t in df["type"].dropna().unique())
    selected_types = f3.multiselect("Type", types, key="fx_types") if types else []
    max_price = f4.number_input(
        f"Max my price ({CURRENCY})", min_value=0.0, value=0.0, step=10.0,
        help="0 = no limit", key="fx_maxprice",
    )

    if text:
        haystack = (df["sku"].fillna("").astype(str) + " " + df["name"].fillna("").astype(str)).str.lower()
        mask = pd.Series(True, index=df.index)
        for term in text.lower().split():
            mask &= haystack.str.contains(term, regex=False)
        df = df[mask]
    if only_stock:
        df = df[df["stock"].fillna(0) > 0]
    if selected_types:
        df = df[df["type"].astype(str).isin(selected_types)]
    if max_price > 0:
        df = df[df["my_price"] <= max_price]

    m1, m2, m3 = st.columns(3)
    m1.metric("Products shown", len(df))
    m2.metric("In stock", int((df["stock"].fillna(0) > 0).sum()))
    m3.metric("Total in catalogue", len(cat["items"]))

    cols = ["sku", "name", "type", "list_price", "my_price", "sell_price", "stock", "parent_sku", "url", "image"]
    st.dataframe(
        df[cols],
        hide_index=True,
        column_config={
            "url": st.column_config.LinkColumn("Link"),
            "image": st.column_config.ImageColumn("Image"),
            "list_price": st.column_config.NumberColumn(f"List price ({CURRENCY})"),
            "my_price": st.column_config.NumberColumn(f"My price ({CURRENCY})"),
            "sell_price": st.column_config.NumberColumn(f"Sell price ({CURRENCY})"),
        },
    )
    st.download_button(
        "Download CSV (current filters)",
        to_csv_bytes(df[cols]),
        file_name=f"flexoptix_catalog_{CURRENCY}.csv",
        mime="text/csv",
        key="fx_dl_catalog",
    )


# --------------------------------------------------------------------------
# Product by part number
# --------------------------------------------------------------------------
def tab_product(margin: float) -> None:
    skus_text = st.text_area(
        "Part numbers (one per line or comma-separated)",
        placeholder="P.8596.02\nP.1396.10",
        height=110,
        key="fx_skus",
    )
    c1, c2 = st.columns(2)
    want_compat = c1.checkbox("Include compatibilities", value=True, key="fx_compat")
    want_specs = c2.checkbox("Include specifications", value=True, key="fx_specs")

    if st.button("Look up products", type="primary", key="fx_lookup"):
        skus = [s.strip() for s in re.split(r"[,\n;]+", skus_text) if s.strip()]
        if not skus:
            st.warning("Enter at least one part number.")
        else:
            params: dict = {"sku[]": skus, "currency": CURRENCY}
            if want_compat:
                params["compatibilities"] = 1
            if want_specs:
                params["specifications"] = 1
            try:
                r = api_get(f"{API_V2}/flexoptix/products", params)
                if not r.ok:
                    st.error(f"HTTP {r.status_code}: {r.text[:300]}")
                else:
                    st.session_state["fx_product"] = {
                        "items": unescape_all(r.json()), "requested": skus,
                    }
            except ApiError as exc:
                st.error(str(exc))

    res = st.session_state.get("fx_product")
    if not res:
        return

    items = res["items"]
    found = {str(i.get("sku")) for i in items}
    missing = [s for s in res["requested"] if s not in found]
    if missing:
        st.warning("Not returned by the API: " + ", ".join(missing))

    for item in items:
        sku = str(item.get("sku"))
        listed, mine, sell = item_prices(item, margin)
        with st.container(border=True):
            st.subheader(f"{sku} · {item.get('name', '')}")
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric(f"List price ({CURRENCY})", listed)
            m2.metric(f"My price ({CURRENCY})", mine)
            m3.metric(f"Sell price ({CURRENCY})", sell)
            m4.metric("Stock", item.get("stock"))
            if item.get("url"):
                m5.link_button("View on Flexoptix", item["url"])

            if item.get("specifications"):
                st.markdown("**Specifications**")
                st.dataframe(pd.DataFrame(item["specifications"]), hide_index=True)

            if item.get("compatibilities"):
                st.markdown("**Compatibilities**")
                cdf = pd.DataFrame(item["compatibilities"])
                vendor = st.text_input(
                    "Filter by vendor", key=f"fx_vendor_{sku}", placeholder="Cisco, Juniper, Arista..."
                )
                if vendor and "compatible_to_vendor" in cdf.columns:
                    cdf = cdf[
                        cdf["compatible_to_vendor"].astype(str).str.contains(vendor, case=False, regex=False)
                    ]
                st.caption(f"{len(cdf)} profiles")
                st.dataframe(cdf, hide_index=True)
                st.download_button(
                    "Download compatibilities (CSV)", to_csv_bytes(cdf),
                    file_name=f"compatibilities_{sku}.csv", mime="text/csv", key=f"fx_dl_{sku}",
                )

            if st.checkbox("Show raw JSON", key=f"fx_json_{sku}"):
                st.json(item)


# --------------------------------------------------------------------------
# Serial numbers (Flexoptix Serial Number API)
# --------------------------------------------------------------------------
SERIAL_HEADERS = {"serial", "serial_number", "serial number", "serialnumber", "sn", "s/n"}

# label in product.specifications -> column in the results table
KEY_SPECS = {
    "Form Factor": "form_factor",
    "Compliance Code": "compliance",
    "Distance": "distance",
    "Wavelength TX (Typical)": "wavelength_tx",
    "Temperature Range": "temp_range",
    "Connector / Polish": "connector",
    "Interface": "fibre",
}

SERIAL_LABELS = {
    "serial": "Serial",
    "status": "Status",
    "part_number": "Part number",
    "full_sku": "Full SKU",
    "product": "Product",
    "form_factor": "Form factor",
    "compliance": "Compliance",
    "distance": "Distance",
    "wavelength_tx": "λ TX (typ.)",
    "temp_range": "Temp. range",
    "connector": "Connector",
    "fibre": "Fibre",
    "delivery_slip": "Delivery slip",
    "delivery_date": "Delivery date",
    "order": "Order",
    "order_date": "Order date",
    "customer_reference": "Customer ref.",
}
SERIAL_COLS = list(SERIAL_LABELS)

# What is copied from the API into an inventory record
SNAPSHOT_KEYS = [
    "part_number", "full_sku", "product", "form_factor", "compliance", "distance",
    "wavelength_tx", "temp_range", "connector", "fibre",
    "delivery_slip", "delivery_date", "order", "order_date", "customer_reference",
]


def parse_serials(text: str, upload) -> list[str]:
    """Serials from the text box (one per line) and/or an uploaded CSV/TXT.

    In an uploaded file, a header called serial / serial_number / sn selects the column;
    otherwise the first column is used.
    """
    serials: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        s = raw.strip().strip('"').strip("'").strip()
        if s and s.lower() not in SERIAL_HEADERS and s not in seen:
            seen.add(s)
            serials.append(s)

    for line in text.splitlines():
        add(re.split(r"[,;\t]", line)[0])

    if upload is not None:
        content = upload.getvalue().decode("utf-8-sig", errors="ignore")
        rows = [re.split(r"[,;\t]", ln) for ln in content.splitlines() if ln.strip()]
        col = 0
        if rows:
            for i, cell in enumerate(rows[0]):
                if cell.strip().strip('"').lower() in SERIAL_HEADERS:
                    col = i
                    rows = rows[1:]
                    break
        for row in rows:
            if col < len(row):
                add(row[col])
    return serials


def normalise_response(data) -> dict | None:
    """The API answers with a list. Take the first result; if the list carries extra
    objects without a serial_number (e.g. delivery / order blocks) merge them in."""
    if isinstance(data, dict):
        return data
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return None
    merged = dict(data[0])
    for extra in data[1:]:
        if isinstance(extra, dict) and "serial_number" not in extra:
            for k, v in extra.items():
                merged.setdefault(k, v)
    return merged


def lookup_serial(serial: str) -> tuple[str, dict | None]:
    r = api_get(f"{API_V1}/flexoptix/serial-number/{quote(serial, safe='')}")
    if r.status_code == 404:
        return "not found", None
    if r.status_code in (401, 403):
        return "access denied (API not activated?)", None
    if not r.ok:
        return f"error HTTP {r.status_code}", None
    item = normalise_response(r.json())
    if not item:
        return "not found", None
    return "ok", unescape_all(item)


def _as_dict(value) -> dict:
    if isinstance(value, list):
        value = value[0] if value else {}
    return value if isinstance(value, dict) else {}


def serial_row(serial: str, item: dict) -> dict:
    product = _as_dict(item.get("product"))
    delivery = _as_dict(item.get("delivery"))
    order = _as_dict(item.get("order"))

    specs: dict = {}
    for spec in product.get("specifications") or []:
        label = spec.get("label")
        if label and label not in specs:  # some labels repeat; keep the first
            specs[label] = spec.get("value")

    row = {
        "serial": item.get("serial_number") or serial,
        "status": "ok",
        "part_number": str(product.get("product_sku") or ""),
        "full_sku": str(item.get("sku") or ""),
        "product": product.get("name", ""),
        "delivery_slip": delivery.get("delivery_slip_number", ""),
        "delivery_date": delivery.get("delivery_slip_date", ""),
        "order": order.get("order_increment_id", ""),
        "order_date": order.get("order_date", ""),
        "customer_reference": order.get("customer_reference", ""),
    }
    for label, col in KEY_SPECS.items():
        row[col] = specs.get(label, "")
    return row


def flexoptix_snapshot(row: dict) -> dict:
    return {k: row.get(k, "") for k in SNAPSHOT_KEYS}


def compat_vendors(product_sku: str) -> list[str]:
    """Vendor names Flexoptix lists as compatible for a product (cached per session)."""
    cache = st.session_state.setdefault("fx_vendors_cache", {})
    if product_sku in cache:
        return cache[product_sku]
    try:
        r = api_get(
            f"{API_V2}/flexoptix/products",
            {"sku[]": [product_sku], "currency": CURRENCY, "compatibilities": 1},
        )
    except ApiError:
        return []
    names: set[str] = set()
    if r.ok:
        for prod in unescape_all(r.json()):
            if str(prod.get("sku")) == product_sku:
                for comp in prod.get("compatibilities") or []:
                    name = str(comp.get("compatible_to_vendor") or "").strip()
                    if name:
                        names.add(name)
        cache[product_sku] = sorted(names)
        return cache[product_sku]
    return []


def tab_serials() -> None:
    st.caption(
        "Looks up each serial number with Flexoptix's Serial Number API. The API answers one "
        "serial at a time and has no “list everything I own” call, so you provide the serial "
        "numbers (from the labels, your stock records or a scan) and it returns the model, "
        "specifications and, when available, the delivery and order each one came from. "
        "It does not return the programming profile."
    )
    text = st.text_area("Serial numbers (one per line)", height=140, key="fx_serials_text")
    upload = st.file_uploader(
        "...or upload a CSV/TXT (column named serial / serial_number / sn, otherwise the first column)",
        type=["csv", "txt"], key="fx_serials_file",
    )
    refresh = st.checkbox("Re-query serials already looked up in this session", key="fx_serials_refresh")

    if st.button("Look up serials", type="primary", key="fx_serials_btn"):
        serials = parse_serials(text, upload)
        if not serials:
            st.warning("No serial numbers to look up.")
        elif len(serials) > 1000:
            st.warning("Maximum 1000 serials per run.")
        else:
            cache = st.session_state.setdefault("fx_serial_cache", {})
            rows: list[dict] = []
            raw: dict = {}
            bar = st.progress(0.0, text="Looking up...")
            try:
                for i, serial in enumerate(serials, 1):
                    if serial in cache and not refresh:
                        status, item = "ok", cache[serial]
                    else:
                        status, item = lookup_serial(serial)
                        if status == "ok":
                            cache[serial] = item
                        time.sleep(0.1)
                    if item is not None:
                        row = serial_row(serial, item)
                        raw[row["serial"]] = item
                        rows.append(row)
                    else:
                        rows.append({"serial": serial, "status": status})
                    bar.progress(i / len(serials), text=f"{i}/{len(serials)}: {serial} → {status}")
            except ApiError as exc:
                st.error(str(exc))
            finally:
                bar.empty()
            st.session_state["fx_serials"] = {"rows": rows, "raw": raw}

    res = st.session_state.get("fx_serials")
    if not res:
        return

    df = pd.DataFrame(res["rows"], columns=SERIAL_COLS).fillna("")
    ok_df = df[df["status"] == "ok"]

    m1, m2, m3 = st.columns(3)
    m1.metric("Found", f"{len(ok_df)} / {len(df)}")
    m2.metric("Not found / errors", len(df) - len(ok_df))
    m3.metric("Different models", ok_df["part_number"].nunique())

    if len(df) > len(ok_df):
        st.caption(
            "“Not found” can mean the serial is not registered yet (older serials may still "
            "be missing), it was mistyped, or the API is not active for your user."
        )

    forms = sorted(f for f in ok_df["form_factor"].unique() if f)
    chosen_forms = st.multiselect("Filter by form factor", forms, key="fx_serials_forms") if forms else []
    view = df
    if chosen_forms:
        view = df[df["form_factor"].isin(chosen_forms)]

    st.markdown("**Units by model**")
    summary = (
        view[view["status"] == "ok"]
        .groupby(["part_number", "product", "form_factor"], dropna=False)
        .size()
        .reset_index(name="units")
        .sort_values("units", ascending=False)
    )
    st.dataframe(
        summary, hide_index=True,
        column_config={
            "part_number": "Part number", "product": "Product",
            "form_factor": "Form factor", "units": "Units",
        },
    )

    st.markdown("**Every serial**")
    st.dataframe(
        view, hide_index=True,
        column_config={col: st.column_config.TextColumn(label) for col, label in SERIAL_LABELS.items()},
    )

    d1, d2 = st.columns(2)
    d1.download_button("Download CSV", to_csv_bytes(view), file_name="flexoptix_serials.csv",
                       mime="text/csv", key="fx_dl_serials_csv")
    d2.download_button(
        "Download full JSON",
        json.dumps(res["raw"], ensure_ascii=False, indent=2).encode("utf-8"),
        file_name="flexoptix_serials.json", mime="application/json", key="fx_dl_serials_json",
    )

    # Send what is listed above to the inventory (one commit, no new API calls)
    listed = [s for s in view.loc[view["status"] == "ok", "serial"] if s in res["raw"]]
    if listed and st.button(f"Add the {len(listed)} listed serials to the inventory", key="fx_serials_to_inv"):
        records = {
            s: inv_db.new_record(s, flexoptix_snapshot(serial_row(s, res["raw"][s]))) for s in listed
        }
        try:
            added, present = inv_db.add_modules(records)
            st.success(f"{added} added to the inventory, {present} were already there.")
        except Exception as exc:  # GitHub / configuration problems
            st.error(f"Could not save to the inventory: {exc}")

    if res["raw"]:
        picked = st.selectbox("Details for one serial", list(res["raw"]), key="fx_serial_pick")
        item = res["raw"][picked]
        specs = _as_dict(item.get("product")).get("specifications") or []
        if specs:
            st.markdown("**All specifications**")
            st.dataframe(pd.DataFrame(specs), hide_index=True)
        if st.checkbox("Show raw JSON", key="fx_serial_rawjson"):
            st.json(item)


# --------------------------------------------------------------------------
# Inventory (stored in the private GitHub repo by inventory_db)
# --------------------------------------------------------------------------
OTHER = "Other…"


def product_images(part_numbers) -> dict:
    """{part_number: image_url}, from the products API (cached per session)."""
    cache = st.session_state.setdefault("fx_img_cache", {})
    missing = sorted({p for p in part_numbers if p and p not in cache})
    for i in range(0, len(missing), 50):
        chunk = missing[i:i + 50]
        try:
            r = api_get(
                f"{API_V2}/flexoptix/products",
                {"sku[]": chunk, "currency": CURRENCY},
            )
        except ApiError:
            continue
        if not r.ok:
            continue
        for prod in unescape_all(r.json()):
            cache[str(prod.get("sku"))] = prod.get("image") or ""
        for p in chunk:
            cache.setdefault(p, "")
    return cache


INV_LABELS = {
    "serial": "Serial",
    "status": "Status",
    "model": "Model",
    "vendor": "Vendor",
    "form_factor": "Form factor",
    "fibre": "Fibre",
    "connector": "Connector",
    "image": "Image",
    "client": "Customer ID",
    "device": "Device / switch",
    "device_type": "Device type",
    "module_bay": "Port",
    "location": "Location",
    "rack": "Rack",
    "updated": "Updated",
}
INV_COLS = list(INV_LABELS)

FLEX_INFO_LABELS = {
    "product": "Product", "part_number": "Part number", "full_sku": "SKU",
    "form_factor": "Form factor", "compliance": "Compliance", "distance": "Distance",
    "wavelength_tx": "λ TX (typ.)", "temp_range": "Temp. range", "connector": "Connector",
    "fibre": "Fibre", "order": "Order", "order_date": "Order date",
    "customer_reference": "Customer ref.", "delivery_slip": "Delivery slip",
    "delivery_date": "Delivery date",
}


def _flex(rec: dict) -> dict:
    return rec.get("flexoptix") or {}


def _model(rec: dict) -> str:
    return rec.get("model") or _flex(rec).get("product", "")


def _inventory_frame(modules: dict) -> pd.DataFrame:
    images = product_images(
        {_flex(r).get("part_number", "") for r in modules.values()}
    )
    rows = []
    for serial, rec in modules.items():
        fx = _flex(rec)
        rows.append({
            "serial": serial,
            "status": rec.get("status", ""),
            "model": _model(rec),
            "vendor": rec.get("vendor", ""),
            "form_factor": fx.get("form_factor", ""),
            "fibre": fx.get("fibre", ""),
            "connector": fx.get("connector", ""),
            "image": images.get(fx.get("part_number", ""), ""),
            "client": rec.get("client", ""),
            "device": rec.get("device", ""),
            "device_type": rec.get("device_type", ""),
            "module_bay": rec.get("module_bay", ""),
            "location": rec.get("location", ""),
            "rack": rec.get("rack", ""),
            "updated": rec.get("updated", ""),
        })
    return pd.DataFrame(rows, columns=INV_COLS).fillna("")


def _summary(serial: str, rec: dict) -> str:
    place = " ".join(p for p in (rec.get("device", ""), rec.get("module_bay", "")) if p)
    parts = [serial, _model(rec)[:40], rec.get("status", ""), place]
    return " · ".join(p for p in parts if p)


def _add_serials_to_inventory(text: str, upload, add_unknown: bool, modules: dict) -> None:
    parsed = parse_serials(text, upload)
    serials = [s for s in parsed if s not in modules]
    skipped = len(parsed) - len(serials)
    if not parsed:
        st.warning("No serial numbers to add: type them or upload a file.")
        return
    if len(parsed) > 1000:
        st.warning("Maximum 1000 serials per run.")
        return
    if not serials:
        st.warning("Nothing to add: all of them are already in the inventory.")
        return

    records: dict = {}
    unknown: list[str] = []
    bar = st.progress(0.0, text="Looking up...")
    try:
        for i, serial in enumerate(serials, 1):
            status, item = lookup_serial(serial)
            if status == "ok":
                row = serial_row(serial, item)
                records[row["serial"]] = inv_db.new_record(row["serial"], flexoptix_snapshot(row))
            else:
                unknown.append(f"{serial} ({status})")
                if add_unknown:
                    records[serial] = inv_db.new_record(serial)
            bar.progress(i / len(serials), text=f"{i}/{len(serials)}: {serial} → {status}")
            time.sleep(0.1)
    except ApiError as exc:
        st.error(str(exc))
        return
    finally:
        bar.empty()

    if not records:
        st.warning("None of the serials were found, so nothing was added. "
                   "Tick “Also add serials Flexoptix does not recognise” to add them by hand.")
        return
    try:
        added, present = inv_db.add_modules(records)
    except Exception as exc:
        st.error(f"Could not save to the inventory: {exc}")
        return

    msg = f"{added} serial(s) added to the inventory."
    if skipped:
        msg += f" {skipped} were already there."
    if unknown:
        msg += " Not recognised by Flexoptix: " + ", ".join(unknown) + "."
    st.session_state["fx_inv_flash"] = msg
    st.rerun()


def _duplicate_module(serial: str, modules: dict) -> None:
    rec = modules[serial]
    fx = _flex(rec)

    def k(field: str) -> str:
        return f"fx_invf_{serial}_{field}"

    with st.expander("Duplicate this module"):
        st.caption(
            "Creates new modules with the same fields as this one (vendor, customer, device, "
            "location, rack, status, comments...). The Flexoptix info is looked up for each new "
            "serial. The port is NOT copied, because a port holds only one module."
        )
        new_text = st.text_area("New serial numbers (one per line)", height=90, key=k("dup_text"))
        new_bay = st.text_input(
            "Port for the new module (only used if you enter a single serial)", key=k("dup_bay")
        )
        if not st.button("Duplicate", key=k("dup_btn")):
            return

        serials = [s for s in parse_serials(new_text, None) if s not in modules]
        if not serials:
            st.warning("No new serial numbers (empty, or they already exist in the inventory).")
            return

        fields = {f: rec.get(f, "") for f in inv_db.TEXT_FIELDS}
        fields["tags"] = list(rec.get("tags") or [])
        fields["module_bay"] = new_bay.strip() if len(serials) == 1 else ""

        # If Flexoptix does not know the new serial, reuse the model data of the original
        # but not its delivery/order data.
        fallback = {
            key: ("" if key in ("order", "order_date", "customer_reference",
                                "delivery_slip", "delivery_date") else v)
            for key, v in fx.items()
        }

        records: dict = {}
        not_found: list[str] = []
        try:
            for s in serials:
                status_api, item = lookup_serial(s)
                if status_api == "ok":
                    row = serial_row(s, item)
                    snap = flexoptix_snapshot(row)
                    records[row["serial"]] = inv_db.new_record(row["serial"], snap, **fields)
                else:
                    not_found.append(s)
                    records[s] = inv_db.new_record(s, fallback, **fields)
                time.sleep(0.1)
        except ApiError as exc:
            st.error(str(exc))
            return

        try:
            added, _present = inv_db.add_modules(records)
        except Exception as exc:
            st.error(f"Could not save to the inventory: {exc}")
            return

        msg = f"{added} module(s) duplicated from {serial}."
        if not_found:
            msg += (" Not recognised by Flexoptix (model data copied from the original): "
                    + ", ".join(not_found) + ".")
        st.session_state["fx_inv_flash"] = msg
        st.rerun()


def _edit_module(serial: str, modules: dict) -> None:
    rec = modules[serial]
    fx = _flex(rec)

    def key(field: str) -> str:
        return f"fx_invf_{serial}_{field}"

    st.markdown(f"#### {serial}")
    left, right = st.columns(2)

    # ── Module (like NetBox: device, bay, status, description, asset tag) ──
    with left:
        statuses = list(inv_db.STATUSES)
        if rec.get("status") and rec["status"] not in statuses:
            statuses.append(rec["status"])
        status = st.selectbox("Status", statuses, index=statuses.index(rec.get("status") or statuses[0]),
                              key=key("status"))
        device = st.text_input("Device / switch ID", value=rec.get("device", ""), key=key("device"))
        device_type = st.text_input("Device type (switch model)", value=rec.get("device_type", ""),
                                    key=key("device_type"))
        module_bay = st.text_input("Port / module bay", value=rec.get("module_bay", ""),
                                   placeholder="Ethernet1/1", key=key("module_bay"))
        asset_tag = st.text_input("Asset tag", value=rec.get("asset_tag", ""), key=key("asset_tag"))
        description = st.text_input("Description", value=rec.get("description", ""), key=key("description"))

    # ── Programming, ownership and place ──
    with right:
        suggestions = set(compat_vendors(fx["part_number"])) if fx.get("part_number") else set()
        suggestions |= {m.get("vendor") for m in modules.values() if m.get("vendor")}
        current_vendor = rec.get("vendor", "")
        if current_vendor:
            suggestions.add(current_vendor)
        options = [""] + sorted(suggestions) + [OTHER]
        vendor_pick = st.selectbox(
            "Vendor (programmed for)", options,
            index=options.index(current_vendor) if current_vendor in options else 0,
            key=key("vendor_pick"),
            help="List taken from the vendors Flexoptix says this model is compatible with, "
                 "plus the ones already used in your inventory.",
        )
        vendor = (st.text_input("Vendor name", key=key("vendor_other")) if vendor_pick == OTHER
                  else vendor_pick)
        profile_code = st.text_input("Programming profile code", value=rec.get("profile_code", ""),
                                     placeholder="e.g. MSA01", key=key("profile_code"))
        client = st.text_input("Customer ID", value=rec.get("client", ""), key=key("client"))
        location = st.text_input("Location (site / room)", value=rec.get("location", ""), key=key("location"))
        rack = st.text_input("Rack / position", value=rec.get("rack", ""), key=key("rack"))
        installed_on = st.text_input("Installed on (YYYY-MM-DD)", value=rec.get("installed_on", ""),
                                     key=key("installed_on"))

    model = rec.get("model", "")
    if not fx.get("part_number"):
        model = st.text_input("Model / part number (Flexoptix does not know this serial)",
                              value=model, key=key("model"))

    tags_text = st.text_input("Tags (comma-separated)", value=", ".join(rec.get("tags") or []),
                              key=key("tags"))
    comments = st.text_area("Comments", value=rec.get("comments", ""), key=key("comments"), height=90)

    # A port can only hold one module: warn (do not block) about clashes
    if device.strip() and module_bay.strip():
        clash = [
            s for s, m in modules.items()
            if s != serial
            and (m.get("device", "").strip().lower(), m.get("module_bay", "").strip().lower())
            == (device.strip().lower(), module_bay.strip().lower())
        ]
        if clash:
            st.warning(f"{device} {module_bay} is already assigned to {', '.join(clash)}.")

    b1, b2, _ = st.columns([1, 1.4, 3])
    if b1.button("Save changes", type="primary", key=key("save")):
        changes = {
            "status": status, "device": device.strip(), "device_type": device_type.strip(),
            "module_bay": module_bay.strip(), "asset_tag": asset_tag.strip(),
            "description": description.strip(), "vendor": (vendor or "").strip(),
            "profile_code": profile_code.strip(), "client": client.strip(),
            "location": location.strip(), "rack": rack.strip(), "installed_on": installed_on.strip(),
            "tags": [t.strip() for t in tags_text.split(",") if t.strip()],
            "comments": comments.strip(), "model": model.strip(),
        }
        try:
            inv_db.save_module(serial, changes)
            st.session_state["fx_inv_flash"] = f"Saved {serial}."
            st.rerun()
        except Exception as exc:
            st.error(f"Could not save: {exc}")

    if b2.button("Refresh Flexoptix info", key=key("refresh")):
        try:
            status_api, item = lookup_serial(serial)
            if status_api != "ok":
                st.error(f"Flexoptix: {status_api}")
            else:
                inv_db.save_module(serial, {"flexoptix": flexoptix_snapshot(serial_row(serial, item))})
                st.session_state["fx_inv_flash"] = f"Flexoptix info refreshed for {serial}."
                st.rerun()
        except Exception as exc:  # st.rerun() is not an Exception, so it passes through
            st.error(str(exc))

    if fx:
        with st.expander("Flexoptix info (read-only)"):
            info = [{"Field": FLEX_INFO_LABELS[k], "Value": fx.get(k, "")} for k in FLEX_INFO_LABELS if fx.get(k)]
            st.dataframe(pd.DataFrame(info), hide_index=True)
    else:
        st.caption("No Flexoptix data for this serial.")

    _duplicate_module(serial, modules)

    with st.expander("Delete this module"):
        confirm = st.checkbox(f"Yes, delete {serial} from the inventory", key=key("confirm_delete"))
        if st.button("Delete module", disabled=not confirm, key=key("delete")):
            try:
                inv_db.delete_module(serial)
                st.session_state.pop("fx_inv_pick", None)
                st.session_state["fx_inv_flash"] = f"Deleted {serial}."
                st.rerun()
            except Exception as exc:
                st.error(f"Could not delete: {exc}")


def tab_inventory() -> None:
    flash = st.session_state.pop("fx_inv_flash", None)
    if flash:
        st.success(flash)

    top1, top2 = st.columns([5, 1])
    top1.caption(
        "Where each module is installed and what it is programmed for, similar to NetBox. "
        "Saved in the private GitHub repository (Propietary_tools/Transceivers/inventory.json)."
    )
    reload = top2.button("Reload from GitHub", key="fx_inv_reload")
    if reload:
        for k in [k for k in st.session_state if str(k).startswith("fx_invf_")]:
            del st.session_state[k]

    try:
        modules = inv_db.load_inventory(force=reload)
    except Exception as exc:
        st.error(f"Could not load the inventory: {exc}")
        return

    m1, m2, m3 = st.columns(3)
    m1.metric("Modules", len(modules))
    m2.metric("Installed", sum(1 for m in modules.values() if m.get("status") == "Installed"))
    m3.metric("In stock", sum(1 for m in modules.values() if m.get("status") == "In stock"))

    with st.expander("➕ Add serials to the inventory"):
        add_text = st.text_area("Serial numbers (one per line)", height=110, key="fx_inv_add_text")
        add_upload = st.file_uploader(
            "...or upload a CSV/TXT (column named serial / serial_number / sn, otherwise the first column)",
            type=["csv", "txt"], key="fx_inv_add_file",
        )
        add_unknown = st.checkbox(
            "Also add serials Flexoptix does not recognise (fill in their details by hand)",
            value=True, key="fx_inv_add_unknown",
        )
        if st.button("Look up and add", key="fx_inv_add_btn"):
            _add_serials_to_inventory(add_text, add_upload, add_unknown, modules)

    if not modules:
        st.info("The inventory is empty. Add serials above, or from the Serials tab.")
        return

    # ── Table with filters ──
    df = _inventory_frame(modules)
    f1, f2, f3 = st.columns([3, 2, 2])
    text = f1.text_input("Search", placeholder="serial, switch, port, customer, location...", key="fx_inv_search")
    statuses = sorted(s for s in df["status"].unique() if s)
    sel_status = f2.multiselect("Status", statuses, key="fx_inv_status_filter")
    devices = sorted(d for d in df["device"].unique() if d)
    sel_device = f3.multiselect("Device / switch", devices, key="fx_inv_device_filter")

    if text:
        hay = df.drop(columns=["updated", "image"]).astype(str).agg(" ".join, axis=1).str.lower()
        mask = pd.Series(True, index=df.index)
        for term in text.lower().split():
            mask &= hay.str.contains(term, regex=False)
        df = df[mask]
    if sel_status:
        df = df[df["status"].isin(sel_status)]
    if sel_device:
        df = df[df["device"].isin(sel_device)]

    column_config = {
        c: st.column_config.TextColumn(l) for c, l in INV_LABELS.items() if c != "image"
    }
    column_config["image"] = st.column_config.ImageColumn("Image")
    st.dataframe(df, hide_index=True, column_config=column_config)
    st.download_button("Download CSV", to_csv_bytes(df), file_name="transceivers_inventory.csv",
                       mime="text/csv", key="fx_inv_dl")

    # ── Edit one module ──
    st.divider()
    st.subheader("Edit a module")
    serials = sorted(modules)
    pick = st.selectbox("Serial number", serials, key="fx_inv_pick",
                        format_func=lambda s: _summary(s, modules[s]))
    _edit_module(pick, modules)


# --------------------------------------------------------------------------
# Entry point (same convention as the other tools)
# --------------------------------------------------------------------------
def show() -> None:
    st.title(":material/cable: Transceivers")

    # Margin lives inside the Catalog tab; the other tabs read the stored value.
    margin = float(st.session_state.get("fx_margin", 20.0))

    # Push the last tab (Catalog) to the far right end of the tab line.
    st.markdown(
        """
        <style>
        /* current Streamlit */
        [data-testid="stTabs"] [role="tablist"] > [data-testid="stTab"]:last-child {
            margin-left: auto;
        }
        /* older Streamlit (BaseWeb tabs) */
        div[data-baseweb="tab-list"] > button[data-baseweb="tab"]:last-of-type {
            margin-left: auto;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    tab_inv, tab_prod, tab_ser, tab_cat = st.tabs(["Inventory", "Product", "Serials", "Catalog"])
    with tab_inv:
        tab_inventory()
    with tab_prod:
        tab_product(margin)
    with tab_ser:
        tab_serials()
    with tab_cat:
        s1, s2, _ = st.columns([1.2, 1, 3])
        margin = s1.number_input(
            "Margin (%)", value=20.0, step=1.0, key="fx_margin",
            help="Sell price = my price × (1 + margin). My price is the self-configure price.",
        )
        s2.write("")  # vertical alignment with the number input
        if s2.button("Test connection", key="fx_test"):
            try:
                get_token(force=True)
                st.success("Connected")
            except ApiError as exc:
                st.error(str(exc))
        tab_catalog(margin)
