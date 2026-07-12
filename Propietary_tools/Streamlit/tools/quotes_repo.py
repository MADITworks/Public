import json
import base64
import pathlib
import requests
import streamlit as st
from datetime import datetime


# ── GitHub config ──────────────────────────────────────────────────────────────
def _headers():
    return {
        "Authorization": f"Bearer {st.secrets['github']['token']}",
        "Accept": "application/vnd.github+json",
    }

def _repo_base():
    # El repo sigue siendo el que ya tenías configurado en secrets
    # (owner/private_repo) — normalmente "Private". No se toca.
    owner = st.secrets["github"]["owner"]
    repo  = st.secrets["github"]["private_repo"]
    return f"https://api.github.com/repos/{owner}/{repo}"


# ── Carpeta base dentro del repo ────────────────────────────────────────────────
# Todo lo relativo a esta app (Quotes/ y Clients/) cuelga de esta carpeta,
# en vez de la raíz del repo.
BASE_PATH = "Propietary_tools"


# ── Leer el índice ─────────────────────────────────────────────────────────────
def _get_index() -> tuple[list, str | None]:
    """Lee {BASE_PATH}/Quotes/index.json. Devuelve (data, sha)."""
    url = f"{_repo_base()}/contents/{BASE_PATH}/Quotes/index.json"
    r   = requests.get(url, headers=_headers())
    if r.status_code == 404:
        return [], None
    r.raise_for_status()
    content = base64.b64decode(r.json()["content"]).decode()
    sha     = r.json()["sha"]
    return json.loads(content), sha


# ── Guardar el índice ──────────────────────────────────────────────────────────
def _save_index(data: list, sha: str | None, message: str):
    url     = f"{_repo_base()}/contents/{BASE_PATH}/Quotes/index.json"
    content = base64.b64encode(
        json.dumps(data, indent=2, ensure_ascii=False).encode()
    ).decode()
    payload = {"message": message, "content": content}
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=_headers(), json=payload)
    r.raise_for_status()


# ── Guardar el Excel original ──────────────────────────────────────────────────
def _upload_excel(filename: str, file_bytes: bytes):
    """Sube el archivo original (xlsx o xls) tal cual, a {BASE_PATH}/Quotes/,
    para poder reabrirlo/descargarlo después desde el histórico."""
    url     = f"{_repo_base()}/contents/{BASE_PATH}/Quotes/{filename}"
    content = base64.b64encode(file_bytes).decode()
    # Verificar si ya existe (para obtener sha)
    r_check = requests.get(url, headers=_headers())
    payload = {
        "message": f"Upload quote {filename}",
        "content": content,
    }
    if r_check.status_code == 200:
        payload["sha"] = r_check.json()["sha"]
    r = requests.put(url, headers=_headers(), json=payload)
    r.raise_for_status()


# ── Guardar / actualizar el detalle completo (meta + items) ───────────────────
def _data_path(record_id: str) -> str:
    return f"{BASE_PATH}/Quotes/data/{record_id}.json"


def _save_detail(record_id: str, detail: dict, message: str):
    url     = f"{_repo_base()}/contents/{_data_path(record_id)}"
    content = base64.b64encode(
        json.dumps(detail, indent=2, ensure_ascii=False, default=str).encode()
    ).decode()
    payload = {"message": message, "content": content}
    r_check = requests.get(url, headers=_headers())
    if r_check.status_code == 200:
        payload["sha"] = r_check.json()["sha"]
    r = requests.put(url, headers=_headers(), json=payload)
    r.raise_for_status()


def load_quote_detail(record_id: str) -> dict:
    """Devuelve el detalle completo (meta, items, form data) de una oferta guardada."""
    url = f"{_repo_base()}/contents/{_data_path(record_id)}"
    r   = requests.get(url, headers=_headers())
    r.raise_for_status()
    content = base64.b64decode(r.json()["content"]).decode()
    return json.loads(content)


# ── Función principal: guardar / actualizar quote ──────────────────────────────
def save_quote(
    client:             str,
    contact:            str,
    email:              str,
    title:              str,
    date:               str,
    meta:               dict,
    items,
    margin_pct:         float,
    distributor:        str,
    file_bytes:         bytes | None = None,
    original_filename:  str = "",
    record_id:          str | None = None,
    contact_title:      str = "",
    contact_mobile:     str = "",
) -> dict:
    """
    Guarda (o actualiza, si se pasa record_id) la oferta en
    {BASE_PATH}/Quotes dentro del repo privado:
    - Sube el Excel original a {BASE_PATH}/Quotes/ (si se provee file_bytes),
      conservando la extensión original (.xlsx o .xls) para que quede
      abrible/legible cuando se descargue más adelante.
    - Guarda el detalle completo (meta + items editados) en
      {BASE_PATH}/Quotes/data/{id}.json
    - Añade / actualiza la entrada en el índice
      {BASE_PATH}/Quotes/index.json, incluyendo el nombre del Excel original
      ("filename"), que queda así enlazado al registro y disponible para
      descarga posterior desde el histórico.
    """
    is_update = record_id is not None
    rid = record_id or datetime.now().strftime("%Y%m%d%H%M%S")

    # Nombre único del archivo — se respeta la extensión original (.xlsx / .xls)
    date_clean   = date.replace("/", "")
    client_clean = client.replace(" ", "-").replace("/", "-")
    quote_num    = meta.get("quote_number", "NOQUOTE")
    ext          = pathlib.Path(original_filename).suffix.lower() or ".xlsx"
    if ext not in (".xlsx", ".xls"):
        ext = ".xlsx"
    filename     = f"{date_clean}_{client_clean}_{quote_num}{ext}"

    if file_bytes:
        _upload_excel(filename, file_bytes)

    cost_total = round(float(items["Total Cost"].sum()), 2)
    sell_total = round(float(cost_total / (1 - margin_pct / 100)), 2)

    record = {
        "id":             rid,
        "date":           date,
        "client":         client,
        "contact":        contact,
        "contact_title":  contact_title,
        "contact_mobile": contact_mobile,
        "email":          email,
        "title":          title,
        "quote_number":   meta.get("quote_number", "—"),
        "expiry":         meta.get("expiry", "—"),
        "currency":       meta.get("currency", "AUD"),
        "distributor":    distributor,
        "margin_pct":     margin_pct,
        "cost_total":     cost_total,
        "sell_total":     sell_total,
        "filename":       filename,   # <- enlace al Excel original, usado por download_quote_excel()
    }

    # Índice (resumen)
    index, sha = _get_index()
    if is_update:
        index = [r for r in index if r.get("id") != rid]
    index.append(record)
    _save_index(index, sha, f"{'Update' if is_update else 'Add'} quote {quote_num} for {client}")

    # Detalle completo (para poder reabrirla exactamente como se guardó)
    detail = {
        **record,
        "meta":  meta,
        "items": items.to_dict(orient="records"),
    }
    _save_detail(rid, detail, f"{'Update' if is_update else 'Add'} quote detail {rid}")

    return record


# ── Cargar historial (resumen) ─────────────────────────────────────────────────
def load_quotes() -> list:
    """Devuelve todas las quotes guardadas (resumen para el listado del historial).
    Cada registro incluye "filename", el Excel original enlazado."""
    index, _ = _get_index()
    return index


# ── Descargar Excel de una quote ───────────────────────────────────────────────
def download_quote_excel(filename: str) -> bytes:
    """Descarga el Excel original de una quote guardada, dado su filename
    (tal y como viene en el registro del índice / detalle)."""
    url = f"{_repo_base()}/contents/{BASE_PATH}/Quotes/{filename}"
    r   = requests.get(url, headers=_headers())
    r.raise_for_status()
    return base64.b64decode(r.json()["content"])


# ── Lista de clientes únicos (derivada del histórico de ofertas) ───────────────
def get_clients() -> list[str]:
    index = load_quotes()
    return sorted(set(q["client"] for q in index if q.get("client")))


# ── Base de datos de clientes / contactos ({BASE_PATH}/Clients/clients.json) ──
def _clients_path() -> str:
    return f"{BASE_PATH}/Clients/clients.json"


def _get_clients_db() -> tuple[dict, str | None]:
    """Lee {BASE_PATH}/Clients/clients.json. Devuelve (data, sha)."""
    url = f"{_repo_base()}/contents/{_clients_path()}"
    r   = requests.get(url, headers=_headers())
    if r.status_code == 404:
        return {}, None
    r.raise_for_status()
    content = base64.b64decode(r.json()["content"]).decode()
    sha     = r.json()["sha"]
    return json.loads(content), sha


def _save_clients_db(data: dict, sha: str | None, message: str):
    url     = f"{_repo_base()}/contents/{_clients_path()}"
    content = base64.b64encode(
        json.dumps(data, indent=2, ensure_ascii=False).encode()
    ).decode()
    payload = {"message": message, "content": content}
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=_headers(), json=payload)
    r.raise_for_status()


def load_clients_db() -> dict:
    """
    Devuelve {client_name: [{"contact": ..., "title": ..., "mobile": ...,
    "email": ...}, ...]}.
    Vacío si el archivo aún no existe. Registros antiguos sin "title"/"mobile"
    siguen funcionando (se leen como cadena vacía).
    """
    data, _ = _get_clients_db()
    return data


def upsert_client_contact(client: str, contact: str, email: str, title: str = "", mobile: str = ""):
    """
    Añade el cliente/contacto si no existe, o actualiza título/móvil/email si el
    contacto ya existía con datos distintos. No falla si todos los campos de
    contacto vienen vacíos (simplemente no agrega una entrada sin nombre).
    """
    client = (client or "").strip()
    if not client:
        return

    contact = (contact or "").strip()
    email   = (email or "").strip()
    title   = (title or "").strip()
    mobile  = (mobile or "").strip()

    data, sha = _get_clients_db()
    contacts = data.get(client, [])

    found = False
    for c in contacts:
        if c.get("contact", "").strip().lower() == contact.lower():
            if email and c.get("email") != email:
                c["email"] = email
            if title and c.get("title") != title:
                c["title"] = title
            if mobile and c.get("mobile") != mobile:
                c["mobile"] = mobile
            found = True
            break

    if not found and (contact or email or title or mobile):
        contacts.append({
            "contact": contact,
            "email":   email,
            "title":   title,
            "mobile":  mobile,
        })

    data[client] = contacts
    _save_clients_db(data, sha, f"Update client contacts for {client}")
