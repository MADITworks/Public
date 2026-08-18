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
    owner = st.secrets["github"]["owner"]
    repo  = st.secrets["github"]["private_repo"]
    return f"https://api.github.com/repos/{owner}/{repo}"


# ── Carpeta base dentro del repo ────────────────────────────────────────────────
# Este módulo es AUTÓNOMO: solo lee/escribe {BASE_PATH}/Quotes/*.
# NO conoce nada de {BASE_PATH}/Clients/ ni {BASE_PATH}/Calendar/.
BASE_PATH = "Propietary_tools"


# ── Estados posibles de una quote ────────────────────────────────────────────────
# "Draft" = creada pero todavía no enviada vía Xero. Es el status con el que
# arranca toda quote nueva (ver save_quote más abajo). El cambio a "Sent" (o
# a cualquier otro estado) es SIEMPRE manual, desde el desplegable de Status
# en Quote History — no hay ninguna lógica automática que lo cambie al
# enviar a Xero, precisamente porque una quote se puede reenviar varias
# veces.
STATUS_DRAFT    = "Draft"
STATUS_SENT     = "Sent"
STATUS_ACCEPTED = "Accepted"
STATUS_REJECTED = "Rejected"
STATUS_EXPIRED  = "Expired"
STATUS_CHOICES  = [STATUS_DRAFT, STATUS_SENT, STATUS_ACCEPTED, STATUS_REJECTED, STATUS_EXPIRED]
# DEFAULT_STATUS se mantiene en "Sent": es el valor de compatibilidad para
# quotes guardadas ANTES de que existiera el campo "status" (ver
# load_quotes más abajo) — esas quotes son de cuando el único flujo posible
# era "ya enviada", así que se siguen tratando como "Sent" por defecto. Las
# quotes NUEVAS usan STATUS_DRAFT explícitamente (ver save_quote).
DEFAULT_STATUS  = STATUS_SENT


# ── Distributor "especial" para quotes manuales de MADIT ───────────────────────
# No es un distribuidor real — es el valor que se guarda en el campo
# "distributor" cuando la oferta la ha hecho MADIT directamente (sin parsear
# un Excel de NEXTGEN/TECHDATA). En ese caso el archivo adjunto (si lo hay)
# puede ser cualquier tipo (ppt, pdf, docx, xlsx...) y el cost/sell total se
# introduce a mano en vez de calcularse a partir de line items + margin_pct.
DISTRIBUTOR_MADIT = "MADIT"


# ── Organización por cliente ─────────────────────────────────────────────────────
def _client_folder(client: str) -> str:
    """Sanitiza el nombre del cliente para usarlo como carpeta en GitHub
    (ej. 'DCS' -> 'DCS', 'M.A.D. Group' -> 'M-A-D-_Group')."""
    name = (client or "Unknown").strip()
    name = re.sub(r"[^A-Za-z0-9 _-]", "-", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = name.replace(" ", "_")
    return name or "Unknown"


def _quote_paths(client: str, filename: str, record_id: str) -> tuple[str | None, str]:
    """Devuelve (excel_path, detail_path) dentro de la carpeta del cliente.
    Si filename está vacío (quote manual de MADIT sin archivo adjunto),
    excel_path es None — no hay nada que subir/descargar."""
    folder      = _client_folder(client)
    excel_path  = f"{BASE_PATH}/Quotes/{folder}/{filename}" if filename else None
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


# ── Guardar el archivo original (Excel de distribuidor, o cualquier fichero
# adjunto en una quote manual de MADIT: ppt, pdf, docx, xlsx...) ──────────────
def _upload_excel(path: str, file_bytes: bytes):
    """Sube el archivo original tal cual, a la ruta ya organizada
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
    manual_cost_total:  float | None = None,
    manual_sell_total:  float | None = None,
) -> dict:
    """
    Guarda (o actualiza, si se pasa record_id) la oferta en
    {BASE_PATH}/Quotes/{Cliente}/ dentro del repo privado:
    - Sube el archivo original (si se pasa file_bytes), con el nombre
      prefijado por fecha (AAAAMMDD) para distributor quotes normales
      (NEXTGEN/TECHDATA), o con su nombre original tal cual cuando
      distributor == DISTRIBUTOR_MADIT (quote manual — el archivo, si lo
      hay, puede ser ppt/pdf/docx/xlsx/lo que sea).
    - Guarda el detalle completo (meta + items editados) en
      {BASE_PATH}/Quotes/{Cliente}/data/{id}.json
    - Añade / actualiza la entrada en el índice global
      {BASE_PATH}/Quotes/index.json (usado por el historial/filtros de la app).
    - Si se está actualizando una oferta y el cliente cambió (o se sube un
      archivo nuevo con distinto nombre), borra los archivos antiguos para
      no dejar duplicados fuera de su carpeta/nombre correcto.
    - Para distributor == DISTRIBUTOR_MADIT, cost_total/sell_total se toman
      directamente de manual_cost_total/manual_sell_total (no se calculan a
      partir de items/margin_pct), y el archivo adjunto es opcional: una
      quote manual puede guardarse sin ningún archivo, solo con los datos
      introducidos a mano.
    - El campo "status" (Draft/Sent/Accepted/Rejected/Expired) se preserva si
      ya existía (edición de una quote guardada), o se pone a "Draft" por
      defecto si es una quote nueva (creada pero aún no enviada vía Xero).
      Para cambiar el estado explícitamente usa set_quote_status(), no este
      método — el cambio de status es siempre manual.
    """
    is_update = record_id is not None
    rid = record_id or datetime.now().strftime("%Y%m%d%H%M%S")

    try:
        date_prefix = datetime.strptime(date, "%d/%m/%Y").strftime("%Y%m%d")
    except ValueError:
        date_prefix = date.replace("/", "")

    # Se necesita el registro anterior (si es una edición) antes de calcular
    # el filename: para quotes MADIT sin archivo nuevo, hay que conservar el
    # archivo (o la ausencia de archivo) que ya tenía.
    index, sha = _get_index()
    old_record = None
    if is_update:
        old_record = next((r for r in index if r.get("id") == rid), None)
        index = [r for r in index if r.get("id") != rid]

    if distributor == DISTRIBUTOR_MADIT:
        if file_bytes and original_filename:
            # Archivo nuevo subido — se conserva su nombre original tal cual,
            # sin prefijo de fecha/número.
            filename = original_filename
        elif not file_bytes and old_record:
            # Edición de una quote manual sin tocar el archivo — se conserva
            # el que ya tenía (o sigue sin archivo si nunca lo tuvo).
            filename = old_record.get("filename", "")
        else:
            # Quote manual sin ningún archivo adjunto — solo datos a mano.
            filename = ""
    else:
        quote_num = meta.get("quote_number", "NOQUOTE")
        ext       = pathlib.Path(original_filename).suffix.lower() or ".xlsx"
        if ext not in (".xlsx", ".xls"):
            ext = ".xlsx"
        filename = f"{date_prefix}_{quote_num}{ext}"

    excel_path, detail_path = _quote_paths(client, filename, rid)

    if file_bytes and excel_path:
        _upload_excel(excel_path, file_bytes)

    if manual_cost_total is not None and manual_sell_total is not None:
        cost_total = round(float(manual_cost_total), 2)
        sell_total = round(float(manual_sell_total), 2)
    else:
        cost_total = round(float(items["Total Cost"].sum()), 2)
        sell_total = round(float(cost_total / (1 - margin_pct / 100)), 2)

    # Una quote recién creada (sin old_record) arranca en "Draft". Al
    # actualizar una quote existente se conserva el status que ya tenía; si
    # por algún motivo una quote antigua no trae status guardado, cae en
    # DEFAULT_STATUS ("Sent"), igual que antes de añadir "Draft".
    status = (
        old_record.get("status", DEFAULT_STATUS) if old_record else STATUS_DRAFT
    )

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
        "filename":       filename,
        "excel_path":     excel_path,
        "detail_path":    detail_path,
    }

    index.append(record)
    _save_index(index, sha, f"{'Update' if is_update else 'Add'} quote {record['quote_number']} for {client}")

    items_records = (
        items.to_dict(orient="records")
        if items is not None and hasattr(items, "empty") and not items.empty
        else []
    )

    detail = {
        **record,
        "meta":  meta,
        "items": items_records,
    }
    _save_detail(detail_path, detail, f"{'Update' if is_update else 'Add'} quote detail {rid}")

    if old_record:
        old_excel  = old_record.get("excel_path")  or (f"{BASE_PATH}/Quotes/{old_record['filename']}" if old_record.get("filename") else None)
        old_detail = old_record.get("detail_path") or f"{BASE_PATH}/Quotes/data/{rid}.json"
        if old_excel and old_excel != excel_path:
            _delete_file(old_excel, f"Remove old file after client/file change for quote {rid}")
        if old_detail and old_detail != detail_path:
            _delete_file(old_detail, f"Remove old detail after client change for quote {rid}")

    return record


# ── Cambiar solo el estado de una quote ya guardada ─────────────────────────────
def delete_quote(record_id: str):
    """Elimina una quote del índice y borra sus archivos asociados (archivo + detalle).

    Es tolerante con quotes antiguas: usa excel_path/detail_path cuando existen y
    mantiene compatibilidad con las rutas planas antiguas. También es tolerante
    con quotes manuales de MADIT sin archivo (excel_path=None)."""
    index, sha = _get_index()
    record = next((r for r in index if r.get("id") == record_id), None)
    if record is None:
        return False

    excel_path = record.get("excel_path")
    if not excel_path and record.get("filename"):
        # Legacy flat path
        excel_path = f"{BASE_PATH}/Quotes/{record['filename']}"

    detail_path = record.get("detail_path") or f"{BASE_PATH}/Quotes/data/{record_id}.json"

    if excel_path:
        _delete_file(excel_path, f"Delete quote file {record_id}")
    if detail_path:
        _delete_file(detail_path, f"Delete quote detail {record_id}")

    new_index = [r for r in index if r.get("id") != record_id]
    _save_index(new_index, sha, f"Delete quote {record_id}")
    return True


def set_quote_status(record_id: str, status: str):
    """Actualiza únicamente el campo 'status' de una quote en el índice
    (Draft / Sent / Accepted / Rejected / Expired). No toca el archivo ni el
    detalle completo — solo reescribe index.json con un único commit."""
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
    """Devuelve todas las quotes guardadas (resumen para el listado del historial).
    Las quotes guardadas antes de añadir el campo 'status' no lo tienen en su
    JSON — quien consuma esta lista debe leerlo con
    q.get("status", DEFAULT_STATUS) para que se traten como "Sent" por defecto."""
    index, _ = _get_index()
    return index


# ── Descargar el archivo original de una quote ─────────────────────────────────
def download_quote_excel(record: dict) -> bytes:
    """Descarga el archivo original de una quote guardada (Excel de
    distribuidor, o el archivo adjunto de una quote manual de MADIT si lo
    tiene). Acepta el registro (dict) tal como viene del índice, historial o
    detalle. Usa 'excel_path' si existe (formato nuevo, organizado por
    cliente); si no, cae de vuelta a la ruta plana antigua (compatibilidad
    con quotes guardadas antes de este cambio). Llamarla en una quote sin
    archivo (excel_path=None y sin filename) es responsabilidad del caller —
    la UI debe comprobar antes si hay archivo (ver campo 'filename')."""
    path = record.get("excel_path") or f"{BASE_PATH}/Quotes/{record.get('filename', '')}"
    url  = f"{_repo_base()}/contents/{path}"
    r    = requests.get(url, headers=_headers())
    r.raise_for_status()
    return base64.b64decode(r.json()["content"])


# ── Lista de clientes únicos (derivada del histórico de ofertas propias) ───────
def get_clients() -> list[str]:
    """Clientes que YA tienen al menos una quote guardada. No tiene relación
    con la base de datos de clientes de tools/clients — es solo un derivado
    interno del índice de Quotes, útil para filtros del historial."""
    index = load_quotes()
    return sorted(set(q["client"] for q in index if q.get("client")))
