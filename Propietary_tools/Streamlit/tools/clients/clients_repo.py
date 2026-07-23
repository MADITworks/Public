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


# ── Base de datos de clientes ({BASE_PATH}/Clients/clients.json) ───────────────
# v2 — esquema nuevo:
#   {
#     "companies": {
#         "Empresa A": {
#             "abn": "", "industry": "", "phone": "", "website": "", "notes": "",
#             "addresses": [
#                 {"label": "Billing", "line1": "", "line2": "", "city": "",
#                  "state": "", "zip": "", "country": ""}
#             ]
#         }
#     },
#     "contacts": {
#         "Empresa A": [{"contact": "...", "title": "...", "mobile": "...", "email": "..."}]
#     }
#   }
#
# Se migra automáticamente desde el formato viejo (v1, plano:
# {empresa: [contactos]}) la primera vez que se lee tras esta actualización.
# La migración no borra ningún contacto — simplemente los mueve intactos a
# data["contacts"], y crea data["companies"][empresa] con los campos nuevos
# vacíos, listos para completarse a mano.
def _clients_path() -> str:
    return f"{BASE_PATH}/Clients/clients.json"


def _empty_company() -> dict:
    return {
        "abn":       "",
        "industry":  "",
        "phone":     "",
        "website":   "",
        "notes":     "",
        "addresses": [],
    }


def _is_v1_format(data: dict) -> bool:
    """El formato v1 es plano: {empresa: [contactos]}. Lo distinguimos del
    v2 porque v2 siempre tiene las claves raíz 'companies' y 'contacts'."""
    return bool(data) and "companies" not in data and "contacts" not in data


def _migrate_v1_to_v2(data: dict) -> dict:
    companies = {}
    contacts  = {}
    for company_name, contact_list in data.items():
        companies[company_name] = _empty_company()
        contacts[company_name]  = contact_list
    return {"companies": companies, "contacts": contacts}


def _get_full_db() -> tuple[dict, str | None]:
    """Lee {BASE_PATH}/Clients/clients.json. Devuelve (data, sha) ya en
    formato v2. Si detecta formato v1, migra en memoria y guarda
    inmediatamente el archivo migrado (un solo commit), para que las
    siguientes lecturas ya encuentren el formato nuevo directamente."""
    url = f"{_repo_base()}/contents/{_clients_path()}"
    r   = requests.get(url, headers=_headers())
    if r.status_code == 404:
        return {"companies": {}, "contacts": {}}, None
    r.raise_for_status()
    content = base64.b64decode(r.json()["content"]).decode()
    sha     = r.json()["sha"]
    raw     = json.loads(content)

    if _is_v1_format(raw):
        migrated = _migrate_v1_to_v2(raw)
        _save_full_db(migrated, sha, "Migrate clients.json to v2 schema (companies + contacts)")
        # Re-lee para obtener el sha nuevo tras el commit de migración.
        return _get_full_db()

    raw.setdefault("companies", {})
    raw.setdefault("contacts", {})
    return raw, sha


def _save_full_db(data: dict, sha: str | None, message: str):
    url     = f"{_repo_base()}/contents/{_clients_path()}"
    content = base64.b64encode(
        json.dumps(data, indent=2, ensure_ascii=False).encode()
    ).decode()
    payload = {"message": message, "content": content}
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=_headers(), json=payload)
    r.raise_for_status()


# ── Lectura compatible con Quotes (NO TOCAR la forma de salida) ────────────────
def load_clients_db() -> dict:
    """
    Devuelve {client_name: [{"contact": ..., "title": ..., "mobile": ...,
    "email": ...}, ...]}.
    Usado por tools/quotes/quotes_clients.py — que SOLO necesita esta forma
    (empresa -> lista de contactos) para poblar los selectbox del Paso 1.
    Vacío si el archivo aún no existe. Se mantiene esta firma/forma tal cual
    para no romper la independencia del módulo Quotes.
    """
    data, _ = _get_full_db()
    return data.get("contacts", {})


# ── Lectura de companies (nuevo) ────────────────────────────────────────────────
def load_companies_db() -> dict:
    """Devuelve {company_name: {"abn": ..., "industry": ..., "phone": ...,
    "website": ..., "notes": ..., "addresses": [...]}}."""
    data, _ = _get_full_db()
    return data.get("companies", {})


# ── CRUD de Companies ───────────────────────────────────────────────────────────
def create_client_company(
    client:    str,
    abn:       str = "",
    industry:  str = "",
    phone:     str = "",
    website:   str = "",
    notes:     str = "",
):
    """Crea una empresa (sin contactos todavía) si aún no existe. Si ya
    existe, no la sobreescribe — usa update_company_info() para editar una
    empresa existente."""
    client = (client or "").strip()
    if not client:
        return
    data, sha = _get_full_db()
    if client not in data["companies"]:
        data["companies"][client] = {
            "abn":       abn.strip(),
            "industry":  industry.strip(),
            "phone":     phone.strip(),
            "website":   website.strip(),
            "notes":     notes.strip(),
            "addresses": [],
        }
        data["contacts"].setdefault(client, [])
        _save_full_db(data, sha, f"Create client company {client}")


def update_company_info(
    client:    str,
    abn:       str = "",
    industry:  str = "",
    phone:     str = "",
    website:   str = "",
    notes:     str = "",
):
    """Actualiza los datos generales de una empresa ya existente (no toca
    direcciones ni contactos)."""
    client = (client or "").strip()
    if not client:
        return
    data, sha = _get_full_db()
    company = data["companies"].setdefault(client, _empty_company())
    company["abn"]      = abn.strip()
    company["industry"] = industry.strip()
    company["phone"]    = phone.strip()
    company["website"]  = website.strip()
    company["notes"]    = notes.strip()
    _save_full_db(data, sha, f"Update company info for {client}")


def rename_client_company(old_name: str, new_name: str):
    """Renombra una empresa (companies + contacts a la vez). Si ya existe
    una empresa con el nuevo nombre, fusiona tanto sus datos generales
    (se conservan los del destino, no se sobreescriben) como sus contactos
    (evitando duplicados por nombre de contacto)."""
    old_name = (old_name or "").strip()
    new_name = (new_name or "").strip()
    if not old_name or not new_name or old_name == new_name:
        return

    data, sha = _get_full_db()
    if old_name not in data["companies"] and old_name not in data["contacts"]:
        return

    old_company = data["companies"].pop(old_name, _empty_company())
    if new_name not in data["companies"]:
        data["companies"][new_name] = old_company

    old_contacts = data["contacts"].pop(old_name, [])
    existing = data["contacts"].get(new_name, [])
    existing_names = {c.get("contact", "").strip().lower() for c in existing}
    for c in old_contacts:
        if c.get("contact", "").strip().lower() not in existing_names:
            existing.append(c)
    data["contacts"][new_name] = existing

    _save_full_db(data, sha, f"Rename client company {old_name} -> {new_name}")


def delete_client_company(client: str):
    """Borra una empresa completa: sus datos generales, direcciones y todos
    sus contactos."""
    client = (client or "").strip()
    data, sha = _get_full_db()
    changed = False
    if client in data["companies"]:
        del data["companies"][client]
        changed = True
    if client in data["contacts"]:
        del data["contacts"][client]
        changed = True
    if changed:
        _save_full_db(data, sha, f"Delete client company {client}")


# ── CRUD de direcciones (múltiples por empresa) ─────────────────────────────────
def add_company_address(
    client:   str,
    label:    str = "",
    line1:    str = "",
    line2:    str = "",
    city:     str = "",
    state:    str = "",
    zip_code: str = "",
    country:  str = "",
):
    """Añade una nueva dirección a la lista de direcciones de una empresa
    (una empresa puede tener varias: Billing, Shipping, distintos edificios,
    etc.)."""
    client = (client or "").strip()
    if not client:
        return
    data, sha = _get_full_db()
    company = data["companies"].setdefault(client, _empty_company())
    company.setdefault("addresses", []).append({
        "label":   label.strip(),
        "line1":   line1.strip(),
        "line2":   line2.strip(),
        "city":    city.strip(),
        "state":   state.strip(),
        "zip":     zip_code.strip(),
        "country": country.strip(),
    })
    _save_full_db(data, sha, f"Add address to {client}")


def update_company_address(
    client:      str,
    index:       int,
    label:       str = "",
    line1:       str = "",
    line2:       str = "",
    city:        str = "",
    state:       str = "",
    zip_code:    str = "",
    country:     str = "",
):
    """Actualiza una dirección existente de una empresa, identificada por su
    índice dentro de la lista 'addresses'."""
    client = (client or "").strip()
    data, sha = _get_full_db()
    addresses = data["companies"].get(client, {}).get("addresses", [])
    if 0 <= index < len(addresses):
        addresses[index] = {
            "label":   label.strip(),
            "line1":   line1.strip(),
            "line2":   line2.strip(),
            "city":    city.strip(),
            "state":   state.strip(),
            "zip":     zip_code.strip(),
            "country": country.strip(),
        }
        _save_full_db(data, sha, f"Update address #{index} for {client}")


def delete_company_address(client: str, index: int):
    """Borra una dirección de una empresa por su índice en la lista."""
    client = (client or "").strip()
    data, sha = _get_full_db()
    addresses = data["companies"].get(client, {}).get("addresses", [])
    if 0 <= index < len(addresses):
        addresses.pop(index)
        _save_full_db(data, sha, f"Delete address #{index} from {client}")


def set_company_addresses(client: str, addresses: list[dict]):
    """Reemplaza la lista COMPLETA de direcciones de una empresa de una sola
    vez. Usado por el formulario de edición de empresa, que gestiona todas
    las direcciones como una tabla editable (múltiples edificios, cada uno
    con su propio label) y las guarda todas juntas al pulsar Save."""
    client = (client or "").strip()
    if not client:
        return
    data, sha = _get_full_db()
    company = data["companies"].setdefault(client, _empty_company())
    clean = []
    for a in addresses:
        label = (a.get("label", "") or "").strip()
        line1 = (a.get("line1", "") or "").strip()
        line2 = (a.get("line2", "") or "").strip()
        city  = (a.get("city", "") or "").strip()
        state = (a.get("state", "") or "").strip()
        zipc  = (a.get("zip", "") or "").strip()
        ctry  = (a.get("country", "") or "").strip()
        # Ignora filas completamente vacías (p.ej. una fila nueva sin rellenar
        # que quedó en la tabla del data_editor).
        if not any([label, line1, line2, city, state, zipc, ctry]):
            continue
        clean.append({
            "label":   label,
            "line1":   line1,
            "line2":   line2,
            "city":    city,
            "state":   state,
            "zip":     zipc,
            "country": ctry,
        })
    company["addresses"] = clean
    _save_full_db(data, sha, f"Set addresses for {client}")


# ── CRUD de Contacts (misma lógica que antes, ahora sobre data["contacts"]) ────
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

    data, sha = _get_full_db()
    contacts = data["contacts"].get(client, [])

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

    data["contacts"][client] = contacts
    _save_full_db(data, sha, f"Update client contacts for {client}")


def delete_client_contact(client: str, contact: str):
    """Borra un contacto específico dentro de una empresa (la empresa
    permanece, aunque quede sin contactos)."""
    client  = (client or "").strip()
    contact = (contact or "").strip()
    data, sha = _get_full_db()
    contacts = data["contacts"].get(client, [])
    new_contacts = [c for c in contacts if c.get("contact", "").strip().lower() != contact.lower()]
    if len(new_contacts) != len(contacts):
        data["contacts"][client] = new_contacts
        _save_full_db(data, sha, f"Delete contact {contact} from {client}")


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

    data, sha = _get_full_db()
    contacts = data["contacts"].get(client, [])

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

    data["contacts"][client] = contacts
    _save_full_db(data, sha, f"Update contact for {client}")
