import streamlit as st
import requests
import base64
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

XERO_AUTH_URL  = "https://login.xero.com/identity/connect/authorize"
XERO_TOKEN_URL = "https://identity.xero.com/connect/token"
XERO_API_BASE  = "https://api.xero.com/api.xro/2.0"
SCOPES         = "openid profile email accounting.invoices accounting.contacts accounting.settings offline_access"

BRANDING_THEME_NAME = "MAD IT WORKS RESELLER"
ITEM_CODE = "MADITworks - PROD"


def get_auth_url() -> str:
    if "xero" not in st.secrets:
        raise KeyError("Xero secrets not configured in secrets.toml")
    cfg = st.secrets["xero"]
    params = {
        "response_type": "code",
        "client_id":     cfg["client_id"],
        "redirect_uri":  cfg["redirect_uri"],
        "scope":         SCOPES,
        "state":         "xero_connect",
    }
    return f"{XERO_AUTH_URL}?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    cfg = st.secrets["xero"]
    credentials = base64.b64encode(
        f"{cfg['client_id']}:{cfg['client_secret']}".encode()
    ).decode()
    resp = requests.post(
        XERO_TOKEN_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type":  "application/x-www-form-urlencoded",
        },
        data={
            "grant_type":   "authorization_code",
            "code":         code,
            "redirect_uri": cfg["redirect_uri"],
        },
        timeout=15,
    )
    resp.raise_for_status()
    tokens = resp.json()
    tokens["expires_at"] = (
        datetime.now(timezone.utc) + timedelta(seconds=tokens.get("expires_in", 1800))
    ).isoformat()
    return tokens


def refresh_tokens(refresh_token: str) -> dict:
    cfg = st.secrets["xero"]
    credentials = base64.b64encode(
        f"{cfg['client_id']}:{cfg['client_secret']}".encode()
    ).decode()
    resp = requests.post(
        XERO_TOKEN_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type":  "application/x-www-form-urlencoded",
        },
        data={
            "grant_type":    "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=15,
    )
    resp.raise_for_status()
    tokens = resp.json()
    tokens["expires_at"] = (
        datetime.now(timezone.utc) + timedelta(seconds=tokens.get("expires_in", 1800))
    ).isoformat()
    return tokens


def get_valid_token() -> str | None:
    tokens = st.session_state.get("xero_tokens")
    if not tokens:
        return None
    expires_at = datetime.fromisoformat(tokens["expires_at"])
    if datetime.now(timezone.utc) >= expires_at - timedelta(minutes=2):
        try:
            tokens = refresh_tokens(tokens["refresh_token"])
            st.session_state["xero_tokens"] = tokens
        except Exception:
            st.session_state.pop("xero_tokens", None)
            return None
    return tokens["access_token"]


def is_connected() -> bool:
    if "xero" not in st.secrets:
        return False
    return get_valid_token() is not None


def handle_callback() -> bool:
    params = st.query_params
    if params.get("state") != "xero_connect" or "code" not in params:
        return False
    code = params["code"]
    try:
        tokens = exchange_code(code)
        st.session_state["xero_tokens"] = tokens
        st.query_params.clear()
        st.rerun()
        return True
    except Exception as e:
        st.error(f"Error al conectar con Xero: {e}")
        st.query_params.clear()
        return False


def get_tenant_id() -> str | None:
    tenant_id = st.session_state.get("xero_tenant_id")
    if tenant_id:
        return tenant_id
    token = get_valid_token()
    if not token:
        return None
    resp = requests.get(
        "https://api.xero.com/connections",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    connections = resp.json()
    if connections:
        st.session_state["xero_tenant_id"] = connections[0]["tenantId"]
        return connections[0]["tenantId"]
    return None


def get_branding_theme_id() -> str | None:
    cached = st.session_state.get("xero_branding_theme_id")
    if cached:
        return cached
    token     = get_valid_token()
    tenant_id = get_tenant_id()
    if not token or not tenant_id:
        return None
    resp = requests.get(
        f"{XERO_API_BASE}/BrandingThemes",
        headers={
            "Authorization":  f"Bearer {token}",
            "Xero-Tenant-Id": tenant_id,
            "Accept":         "application/json",
        },
        timeout=10,
    )
    resp.raise_for_status()
    for theme in resp.json().get("BrandingThemes", []):
        if theme.get("Name", "").strip().upper() == BRANDING_THEME_NAME.upper():
            st.session_state["xero_branding_theme_id"] = theme["BrandingThemeID"]
            return theme["BrandingThemeID"]
    return None


def create_draft_quote(meta: dict, items, margin_pct: float) -> dict:
    token     = get_valid_token()
    tenant_id = get_tenant_id()
    if not token or not tenant_id:
        raise RuntimeError("Xero is not connected.")

    m     = margin_pct / 100.0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    line_items = []
    for _, row in items.iterrows():
        try:
            unit_cost  = float(row["Unit Cost"])
            unit_price = round(unit_cost / (1 - m), 4) if m < 1 else unit_cost
            qty        = int(row["Qty"])
        except (ValueError, TypeError):
            continue
        line_items.append({
            "ItemCode":    ITEM_CODE,
            "Description": f"[{str(row.get('SKU', ''))}] {str(row.get('Description', ''))}",
            "Quantity":    qty,
            "UnitAmount":  unit_price,
            "AccountCode": "200",
        })

    contact_name = meta.get("end_user", "").strip() or "TBD"

    quote_payload = {
        "Status":          "DRAFT",
        "Date":            today,
        "CurrencyCode":    meta.get("currency", "AUD") or "AUD",
        "LineAmountTypes": "EXCLUSIVE",
        "Contact":         {"Name": contact_name},
        "LineItems":       line_items,
        "Title":           f"Quote {meta.get('quote_number', '')}",
        "Summary":         meta.get("description", ""),
        "Reference":       meta.get("quote_number", ""),
    }

    branding_id = get_branding_theme_id()
    if branding_id:
        quote_payload["BrandingThemeID"] = branding_id

    resp = requests.post(
        f"{XERO_API_BASE}/Quotes",
        headers={
            "Authorization":  f"Bearer {token}",
            "Xero-Tenant-Id": tenant_id,
            "Content-Type":   "application/json",
            "Accept":         "application/json",
        },
        json={"Quotes": [quote_payload]},
        timeout=15,
    )
    resp.raise_for_status()
    result = resp.json()
    quotes = result.get("Quotes", [])
    if quotes:
        return quotes[0]
    raise RuntimeError("Xero did not return a valid quote.")
