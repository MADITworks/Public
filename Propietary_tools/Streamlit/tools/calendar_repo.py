import json
import base64
import requests
import streamlit as st
from datetime import datetime


# ── GitHub config (mismo patrón que quotes_repo.py) ─────────────────────────────
def _headers():
    return {
        "Authorization": f"Bearer {st.secrets['github']['token']}",
        "Accept": "application/vnd.github+json",
    }


def _repo_base():
    owner = st.secrets["github"]["owner"]
    repo  = st.secrets["github"]["private_repo"]
    return f"https://api.github.com/repos/{owner}/{repo}"


BASE_PATH     = "Propietary_tools"
CALENDAR_PATH = f"{BASE_PATH}/Calendar/events.json"


# ── Leer / guardar el índice de eventos ─────────────────────────────────────────
def _get_events_raw() -> tuple[list, str | None]:
    url = f"{_repo_base()}/contents/{CALENDAR_PATH}"
    r   = requests.get(url, headers=_headers())
    if r.status_code == 404:
        return [], None
    r.raise_for_status()
    content = base64.b64decode(r.json()["content"]).decode()
    sha     = r.json()["sha"]
    return json.loads(content), sha


def _save_events_raw(data: list, sha: str | None, message: str):
    url     = f"{_repo_base()}/contents/{CALENDAR_PATH}"
    content = base64.b64encode(
        json.dumps(data, indent=2, ensure_ascii=False).encode()
    ).decode()
    payload = {"message": message, "content": content}
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=_headers(), json=payload)
    r.raise_for_status()


def load_events() -> list:
    """Devuelve todos los eventos guardados (cada uno con su lista de recordatorios)."""
    events, _ = _get_events_raw()
    return events


# ── Guardar / actualizar un evento ───────────────────────────────────────────────
def save_event(
    event_name: str,
    event_date: str,          # "DD/MM/YYYY"
    client:     str = "",
    contact:    str = "",
    notes:      str = "",
    reminders:  list[dict] | None = None,   # [{"id":..., "date":"DD/MM/YYYY", "done": bool}, ...]
    event_id:   str | None = None,
) -> dict:
    is_update = event_id is not None
    eid = event_id or datetime.now().strftime("%Y%m%d%H%M%S%f")

    record = {
        "id":         eid,
        "event_name": event_name,
        "date":       event_date,
        "client":     client,
        "contact":    contact,
        "notes":      notes,
        "reminders":  reminders or [],
    }

    events, sha = _get_events_raw()
    events = [e for e in events if e.get("id") != eid]
    events.append(record)
    _save_events_raw(
        events, sha,
        f"{'Update' if is_update else 'Add'} calendar event '{event_name}' ({event_date})",
    )
    return record


# ── Borrar un evento completo ────────────────────────────────────────────────────
def delete_event(event_id: str):
    events, sha = _get_events_raw()
    new_events = [e for e in events if e.get("id") != event_id]
    if len(new_events) != len(events):
        _save_events_raw(new_events, sha, f"Delete calendar event {event_id}")


# ── Marcar un recordatorio individual como "hecho" (para el banner de avisos) ───
def mark_reminder_done(event_id: str, reminder_id: str):
    events, sha = _get_events_raw()
    changed = False
    for e in events:
        if e.get("id") == event_id:
            for r in e.get("reminders", []):
                if r.get("id") == reminder_id:
                    r["done"] = True
                    changed = True
    if changed:
        _save_events_raw(events, sha, f"Dismiss reminder {reminder_id} (event {event_id})")
