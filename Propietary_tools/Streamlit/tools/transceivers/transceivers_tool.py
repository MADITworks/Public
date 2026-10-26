"""MADIT | Transceivers tool (Flexoptix API).

Page for the MADIT internal portal. Exposes show(), like the other tools.

Tabs:
  1. Catalog  - full Flexoptix catalogue in USD: list price, my price (self-configure),
                sell price (my price + margin), filters, CSV export.
  2. Product  - lookup by part number with specifications and compatibilities.
  3. Serials  - lookup by serial number (needs the Serial Number API to be activated
                by Flexoptix for your account).

Credentials: Streamlit secrets, section [flexoptix]:

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
# Serial numbers
# --------------------------------------------------------------------------
def parse_serials(text: str, upload) -> list[str]:
    lines = text.splitlines()
    if upload is not None:
        lines += upload.getvalue().decode("utf-8-sig", errors="ignore").splitlines()
    serials, seen = [], set()
    for line in lines:
        s = re.split(r"[,;\t]", line)[0].strip()
        if not s or s.lower() == "serial" or s in seen:
            continue
        seen.add(s)
        serials.append(s)
    return serials


def lookup_serial(serial: str) -> tuple[str, dict | None]:
    r = api_get(f"{API_V1}/flexoptix/serial-number/{quote(serial, safe='')}")
    if r.status_code == 404:
        return "not found", None
    if r.status_code in (401, 403):
        return "access denied (API not activated?)", None
    if not r.ok:
        return f"error HTTP {r.status_code}", None
    data = r.json()
    item = data[0] if isinstance(data, list) and data else data
    if not item:
        return "not found", None
    return "ok", unescape_all(item)


def tab_serials() -> None:
    st.caption(
        "Uses Flexoptix's Serial Number API. Access has to be requested from Flexoptix "
        "(Transceiver Lookup or Service Desk) and activated for your API user. "
        "A “not found” can mean the serial is too old to be registered yet, or the API is not active."
    )
    text = st.text_area("Serial numbers (one per line)", height=140, key="fx_serials_text")
    upload = st.file_uploader("...or upload a CSV/TXT (first column is used)", type=["csv", "txt"], key="fx_serials_file")

    if st.button("Look up serials", type="primary", key="fx_serials_btn"):
        serials = parse_serials(text, upload)
        if not serials:
            st.warning("No serial numbers to look up.")
        elif len(serials) > 1000:
            st.warning("Maximum 1000 serials per run.")
        else:
            rows, raw = [], {}
            bar = st.progress(0.0, text="Looking up...")
            try:
                for i, serial in enumerate(serials, 1):
                    status, item = lookup_serial(serial)
                    row = {"serial": serial, "status": status}
                    if item is not None:
                        raw[serial] = item
                        row.update(flatten(item))
                    rows.append(row)
                    bar.progress(i / len(serials), text=f"{i}/{len(serials)}: {serial} → {status}")
                    time.sleep(0.2)
                st.session_state["fx_serials"] = {"rows": rows, "raw": raw}
            except ApiError as exc:
                st.error(str(exc))
            finally:
                bar.empty()

    res = st.session_state.get("fx_serials")
    if not res:
        return

    df = pd.DataFrame(res["rows"])
    ok = int((df["status"] == "ok").sum())
    st.metric("Found", f"{ok} / {len(df)}")
    st.dataframe(df, hide_index=True)

    d1, d2 = st.columns(2)
    d1.download_button("Download CSV", to_csv_bytes(df), file_name="flexoptix_serials.csv",
                       mime="text/csv", key="fx_dl_serials_csv")
    d2.download_button(
        "Download full JSON",
        json.dumps(res["raw"], ensure_ascii=False, indent=2).encode("utf-8"),
        file_name="flexoptix_serials.json", mime="application/json", key="fx_dl_serials_json",
    )

    if res["raw"]:
        chosen = st.selectbox("View the raw response for a serial", list(res["raw"]), key="fx_serial_pick")
        st.json(res["raw"][chosen])


# --------------------------------------------------------------------------
# Entry point (same convention as the other tools)
# --------------------------------------------------------------------------
def show() -> None:
    st.title("📡 Transceivers")
    st.caption("Flexoptix catalogue, product compatibilities and serial number lookup. Prices in USD.")

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

    tab1, tab2, tab3 = st.tabs(["Catalog", "Product", "Serials"])
    with tab1:
        tab_catalog(margin)
    with tab2:
        tab_product(margin)
    with tab3:
        tab_serials()
