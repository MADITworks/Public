import json
import base64
import requests
import streamlit as st


# ── GitHub config ──────────────────────────────────────────────────────────────
def _headers():
    return {
        "Authorization": f"Bearer {st.secrets['github']['token']}",
        "Accept": "application/vnd.github+json",
    }

def _repo_base():
    owner = st.secrets["github"]["owner"]
    repo  = st.secrets["github"]["private_repo"]
    return f"https://api.github.com/repos/{owner}/{repo}"


# ── Carpeta base dentro del repo ────────────────────────────────────────────────
# Este módulo es AUTÓNOMO: solo lee/escribe {BASE_PATH}/Clients/*.
# NO conoce nada de {BASE_PATH}/Quotes/ ni {BASE_PATH}/Calendar/.
BASE_PATH = "Propietary_tools"


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


# ── CRUD adicional para la página de administración de clientes (Clients.py) ──
def create_client_company(client: str):
    """Crea una empresa vacía (sin contactos todavía) si aún no existe."""
    client = (client or "").strip()
    if not client:
        return
    data, sha = _get_clients_db()
    if client not in data:
        data[client] = []
        _save_clients_db(data, sha, f"Create client company {client}")


def rename_client_company(old_name: str, new_name: str):
    """Renombra una empresa. Si ya existe una empresa con el nuevo nombre,
    fusiona los contactos (evitando duplicados por nombre de contacto)."""
    old_name = (old_name or "").strip()
    new_name = (new_name or "").strip()
    if not old_name or not new_name or old_name == new_name:
        return

    data, sha = _get_clients_db()
    if old_name not in data:
        return

    contacts = data.pop(old_name)
    existing = data.get(new_name, [])
    existing_names = {c.get("contact", "").strip().lower() for c in existing}
    for c in contacts:
        if c.get("contact", "").strip().lower() not in existing_names:
            existing.append(c)
    data[new_name] = existing
    _save_clients_db(data, sha, f"Rename client company {old_name} -> {new_name}")


def delete_client_company(client: str):
    """Borra una empresa completa junto con todos sus contactos."""
    client = (client or "").strip()
    data, sha = _get_clients_db()
    if client in data:
        del data[client]
        _save_clients_db(data, sha, f"Delete client company {client}")


def delete_client_contact(client: str, contact: str):
    """Borra un contacto específico dentro de una empresa (la empresa
    permanece, aunque quede sin contactos)."""
    client  = (client or "").strip()
    contact = (contact or "").strip()
    data, sha = _get_clients_db()
    contacts = data.get(client, [])
    new_contacts = [c for c in contacts if c.get("contact", "").strip().lower() != contact.lower()]
    if len(new_contacts) != len(contacts):
        data[client] = new_contacts
        _save_clients_db(data, sha, f"Delete contact {contact} from {client}")


def update_client_contact(
    client:      str,
    old_contact: str,
    new_contact: str,
    email:       str = "",
    title:       str = "",
    mobile:      str = "",
):
    """
    Crea o actualiza (incluyendo renombrar) un contacto dentro de una empresa.
    - Si 'old_contact' viene vacío -> se trata como contacto nuevo.
    - Si 'old_contact' viene lleno -> busca ese contacto y lo actualiza,
      incluyendo el posible cambio de nombre a 'new_contact'.
    """
    client = (client or "").strip()
    if not client:
        return

    old_contact = (old_contact or "").strip()
    new_contact = (new_contact or "").strip()
    email       = (email or "").strip()
    title       = (title or "").strip()
    mobile      = (mobile or "").strip()

    data, sha = _get_clients_db()
    contacts = data.get(client, [])

    updated = False
    if old_contact:
        for c in contacts:
            if c.get("contact", "").strip().lower() == old_contact.lower():
                c["contact"] = new_contact or old_contact
                c["email"]   = email
                c["title"]   = title
                c["mobile"]  = mobile
                updated = True
                break

    if not updated and (new_contact or email or title or mobile):
        contacts.append({
            "contact": new_contact,
            "email":   email,
            "title":   title,
            "mobile":  mobile,
        })

    data[client] = contacts
    _save_clients_db(data, sha, f"Update contact for {client}")
