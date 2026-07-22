import json
import base64
import pathlib
import re
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
# Solo lo relativo a QUOTES cuelga de aquí. La gestión de clientes
# (Clients/clients.json) ya NO vive en este módulo — ver clients_lookup.py
# para el único punto de lectura (solo-lectura) que usa Quotes, y el módulo
# Clientes para todo el CRUD real.
BASE_PATH = "Propietary_tools"


# ── Estados posibles de una quote ────────────────────────────────────────────────
# "Sent" es el valor por defecto: se asigna automáticamente a las quotes
# nuevas y también es lo que se devuelve para cualquier quote guardada ANTES
# de este cambio (no tienen la clave "status" en su JSON, así que se leen
# como "Sent" sin necesidad de migrar nada a mano).
STATUS_SENT     = "Sent"
STATUS_ACCEPTED = "Accepted"
STATUS_REJECTED = "Rejected"
STATUS_EXPIRED  = "Expired"
STATUS_CHOICES  = [STATUS_SENT, STATUS_ACCEPTED, STATUS_REJECTED, STATUS_EXPIRED]
DEFAULT_STATUS  = STATUS_SENT


# ── Organización por cliente ─────────────────────────────────────────────────────
def _client_folder(client: str) -> str:
    """Sanitiza el nombre del cliente para usarlo como carpeta en GitHub
    (ej. 'DCS' -> 'DCS', 'M.A.D. Group' -> 'M-A-D-_Group').

    NOTA: esto es puramente una sanitización de string para nombrar carpetas
    dentro de Quotes/ — no consulta ni depende del módulo Clientes. El
    "client" que llega aquí es simplemente el string ya validado en el
    Paso 1 (client_contact_step.py) contra la lista de clientes existentes."""
    name = (client or "Unknown").strip()
    name = re.sub(r"[^A-Za-z0-9 _-]", "-", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = name.replace(" ", "_")
    return name or "Unknown"


def _quote_paths(client: str, filename: str, record_id: str) -> tuple[str, str]:
    """Devuelve (excel_path, detail_path) dentro de la carpeta del cliente."""
    folder      = _client_folder(client)
    excel_path  = f"{BASE_PATH}/Quotes/{folder}/{filename}"
    detail_path = f"{BASE_PATH}/Quotes/{folder}/data/{record_id}.json"
    return excel_path, detail_path


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
def _upload_excel(path: str, file_bytes: bytes):
    """Sube el archivo original (xlsx o xls) tal cual, a la ruta ya organizada
    por cliente, para poder reabrirlo/descargarlo después."""
    url     = f"{_repo_base()}/contents/{path}"
    content = base64.b64encode(file_bytes).decode()
    r_check = requests.get(url, headers=_headers())
    payload = {"message": f"Upload quote file {pathlib.Path(path).name}", "content": content}
    if r_check.status_code == 200:
        payload["sha"] = r_check.json()["sha"]
    r = requests.put(url, headers=_headers(), json=payload)
    r.raise_for_status()


# ── Guardar / actualizar el detalle completo (meta + items) ───────────────────
def _save_detail(path: str, detail: dict, message: str):
    url     = f"{_repo_base()}/contents/{path}"
    content = base64.b64encode(
        json.dumps(detail, indent=2, ensure_ascii=False, default=str).encode()
    ).decode()
    payload = {"message": message, "content": content}
    r_check = requests.get(url, headers=_headers())
    if r_check.status_code == 200:
        payload["sha"] = r_check.json()["sha"]
    r = requests.put(url, headers=_headers(), json=payload)
    r.raise_for_status()


# ── Borrar un archivo (usado para limpiar rutas huérfanas si cambia el cliente) ─
def _delete_file(path: str, message: str):
    url = f"{_repo_base()}/contents/{path}"
    r   = requests.get(url, headers=_headers())
    if r.status_code != 200:
        return  # ya no existe / nunca existió, nada que borrar
    sha = r.json()["sha"]
    requests.delete(url, headers=_headers(), json={"message": message, "sha": sha})


def load_quote_detail(record: dict) -> dict:
    """Devuelve el detalle completo (meta, items, form data) de una oferta guardada.
    Acepta el registro (dict) tal como viene del índice o del historial.
    Usa 'detail_path' si existe (formato nuevo, organizado por cliente);
    si no, cae de vuelta a la ruta plana antigua (compatibilidad con quotes
    guardadas antes de este cambio)."""
    path = record.get("detail_path") or f"{BASE_PATH}/Quotes/data/{record['id']}.json"
    url  = f"{_repo_base()}/contents/{path}"
    r    = requests.get(url, headers=_headers())
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
    {BASE_PATH}/Quotes/{Cliente}/ dentro del repo privado:
    - Sube el Excel original manteniendo su extensión (.xlsx / .xls), con el
      nombre prefijado por fecha (AAAAMMDD) para que queden ordenados
      cronológicamente dentro de la carpeta del cliente.
    - Guarda el detalle completo (meta + items editados) en
      {BASE_PATH}/Quotes/{Cliente}/data/{id}.json
    - Añade / actualiza la entrada en el índice global
      {BASE_PATH}/Quotes/index.json (usado por el historial/filtros de la app).
      El campo "client" de esta entrada es el que usa _show_history() en
      quotes.py para agrupar — eso no cambia.
    - Si se está actualizando una oferta y el cliente cambió, borra los
      archivos antiguos para no dejar duplicados fuera de su carpeta correcta.
    - El campo "status" (Sent/Accepted/Rejected/Expired) se preserva si ya
      existía (edición de una quote guardada), o se pone a "Sent" por
      defecto si es una quote nueva. Para cambiar el estado explícitamente
      usa set_quote_status(), no este método.

    NOTA: 'client' y 'contact' aquí son strings ya validados contra el
    módulo Clientes en el Paso 1 (client_contact_step.py + clients_lookup.py)
    — esta función no valida ni crea nada en clients.json, solo los usa
    como texto para organizar/etiquetar la quote.
    """
    is_update = record_id is not None
    rid = record_id or datetime.now().strftime("%Y%m%d%H%M%S")

    # Prefijo de fecha en formato AAAAMMDD (orden cronológico correcto al
    # listar los archivos dentro de la carpeta del cliente en GitHub)
    try:
        date_prefix = datetime.strptime(date, "%d/%m/%Y").strftime("%Y%m%d")
    except ValueError:
        date_prefix = date.replace("/", "")

    quote_num = meta.get("quote_number", "NOQUOTE")
    ext       = pathlib.Path(original_filename).suffix.lower() or ".xlsx"
    if ext not in (".xlsx", ".xls"):
        ext = ".xlsx"
    filename = f"{date_prefix}_{quote_num}{ext}"

    excel_path, detail_path = _quote_paths(client, filename, rid)

    if file_bytes:
        _upload_excel(excel_path, file_bytes)

    cost_total = round(float(items["Total Cost"].sum()), 2)
    sell_total = round(float(cost_total / (1 - margin_pct / 100)), 2)

    # Índice (resumen)
    index, sha = _get_index()
    old_record = None
    if is_update:
        old_record = next((r for r in index if r.get("id") == rid), None)
        index = [r for r in index if r.get("id") != rid]

    # Preserva el estado existente al editar; por defecto "Sent" si es nueva.
    status = (old_record.get("status", DEFAULT_STATUS) if old_record else DEFAULT_STATUS)

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
        "status":         status,
        "filename":       filename,     # nombre de archivo (display)
        "excel_path":     excel_path,   # ruta completa dentro del repo
        "detail_path":    detail_path,  # ruta completa del detalle
    }

    index.append(record)
    _save_index(index, sha, f"{'Update' if is_update else 'Add'} quote {quote_num} for {client}")

    # Detalle completo (para poder reabrirla exactamente como se guardó)
    detail = {
        **record,
        "meta":  meta,
        "items": items.to_dict(orient="records"),
    }
    _save_detail(detail_path, detail, f"{'Update' if is_update else 'Add'} quote detail {rid}")

    # Si el cliente cambió en una actualización, la oferta ahora vive en una
    # carpeta distinta — limpiamos los archivos viejos para evitar duplicados.
    if old_record:
        old_excel  = old_record.get("excel_path")  or (f"{BASE_PATH}/Quotes/{old_record['filename']}" if old_record.get("filename") else None)
        old_detail = old_record.get("detail_path") or f"{BASE_PATH}/Quotes/data/{rid}.json"
        if old_excel and old_excel != excel_path:
            _delete_file(old_excel, f"Remove old file after client change for quote {rid}")
        if old_detail and old_detail != detail_path:
            _delete_file(old_detail, f"Remove old detail after client change for quote {rid}")

    return record


# ── Cambiar solo el estado de una quote ya guardada ─────────────────────────────
def set_quote_status(record_id: str, status: str):
    """Actualiza únicamente el campo 'status' de una quote en el índice
    (Sent / Accepted / Rejected / Expired). No toca el Excel ni el detalle
    completo — solo reescribe index.json con un único commit."""
    if status not in STATUS_CHOICES:
        raise ValueError(f"Invalid status '{status}'. Must be one of {STATUS_CHOICES}")

    index, sha = _get_index()
    changed = False
    for r in index:
        if r.get("id") == record_id:
            r["status"] = status
            changed = True
    if changed:
        _save_index(index, sha, f"Set status={status} for quote {record_id}")


# ── Cargar historial (resumen) ─────────────────────────────────────────────────
def load_quotes() -> list:
    """Devuelve todas las quotes guardadas (resumen para el listado del historial,
    incluido el agrupado por cliente en _show_history()). Las quotes guardadas
    antes de añadir el campo 'status' no lo tienen en su JSON — quien consuma
    esta lista debe leerlo con q.get("status", DEFAULT_STATUS) para que se
    traten como "Sent" por defecto."""
    index, _ = _get_index()
    return index


# ── Descargar Excel de una quote ───────────────────────────────────────────────
def download_quote_excel(record: dict) -> bytes:
    """Descarga el Excel original de una quote guardada. Acepta el registro
    (dict) tal como viene del índice, historial o detalle. Usa 'excel_path'
    si existe (formato nuevo, organizado por cliente); si no, cae de vuelta
    a la ruta plana antigua (compatibilidad con quotes guardadas antes de
    este cambio)."""
    path = record.get("excel_path") or f"{BASE_PATH}/Quotes/{record.get('filename', '')}"
    url  = f"{_repo_base()}/contents/{path}"
    r    = requests.get(url, headers=_headers())
    r.raise_for_status()
    return base64.b64decode(r.json()["content"])


# ── Lista de clientes únicos (derivada del histórico de ofertas ya guardadas) ──
def get_clients() -> list[str]:
    """Clientes que YA tienen al menos una quote guardada — distinto de la
    lista de clientes disponibles para crear una quote nueva (esa viene de
    clients_lookup.load_clients_readonly(), no de aquí)."""
    index = load_quotes()
    return sorted(set(q["client"] for q in index if q.get("client")))
