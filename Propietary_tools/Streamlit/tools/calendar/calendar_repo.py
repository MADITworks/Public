import json
import base64
import requests
import streamlit as st
from datetime import datetime


# ── GitHub config (same pattern as quotes_repo.py) ──────────────────────────────
def _headers():
    return {
        "Authorization": f"Bearer {st.secrets['github']['token']}",
        "Accept": "application/vnd.github+json",
    }


def _repo_base():
    owner = st.secrets["github"]["owner"]
    repo  = st.secrets["github"]["private_repo"]
    return f"https://api.github.com/repos/{owner}/{repo}"


BASE_PATH        = "Propietary_tools"
CALENDAR_PATH    = f"{BASE_PATH}/Calendar/events.json"
ATTACHMENTS_PATH = f"{BASE_PATH}/Calendar/attachments"


# ── Read / save the events index ────────────────────────────────────────────────
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
    """Returns all saved events (each one with its list of reminders and attachments)."""
    events, _ = _get_events_raw()
    return events


# ── Save / update an event ───────────────────────────────────────────────────────
def save_event(
    event_name:  str,
    event_date:  str,          # "DD/MM/YYYY"
    client:      str = "",
    contact:     str = "",
    notes:       str = "",
    reminders:   list[dict] | None = None,     # [{"id":..., "date":"DD/MM/YYYY", "done": bool}, ...]
    attachments: list[dict] | None = None,     # [{"id":..., "name":..., "path":..., "sha":..., "size":...}, ...]
    event_id:    str | None = None,
) -> dict:
    is_update = event_id is not None
    eid = event_id or datetime.now().strftime("%Y%m%d%H%M%S%f")

    events, sha = _get_events_raw()
    existing = next((e for e in events if e.get("id") == eid), None)

    record = {
        "id":          eid,
        "event_name":  event_name,
        "date":        event_date,
        "client":      client,
        "contact":     contact,
        "notes":       notes,
        "reminders":   reminders or [],
        "attachments": attachments if attachments is not None
                        else (existing.get("attachments", []) if existing else []),
    }

    events = [e for e in events if e.get("id") != eid]
    events.append(record)
    _save_events_raw(
        events, sha,
        f"{'Update' if is_update else 'Add'} calendar event '{event_name}' ({event_date})",
    )
    return record


# ── Delete a full event (and its attachments) ───────────────────────────────────
def delete_event(event_id: str):
    events, sha = _get_events_raw()
    target     = next((e for e in events if e.get("id") == event_id), None)
    new_events = [e for e in events if e.get("id") != event_id]

    if len(new_events) != len(events):
        _save_events_raw(new_events, sha, f"Delete calendar event {event_id}")

    if target:
        for att in target.get("attachments", []):
            try:
                delete_attachment(event_id, att)
            except Exception:
                pass


# ── Mark a single reminder as "done" (for the pending-reminders banner) ────────
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


# ── Attachments ──────────────────────────────────────────────────────────────────
def upload_attachment(event_id: str, filename: str, file_bytes: bytes) -> dict:
    """Uploads a file to the repo under the event's folder and returns its metadata."""
    safe_name = filename.replace("/", "_").replace("\\", "_")
    path      = f"{ATTACHMENTS_PATH}/{event_id}/{safe_name}"
    url       = f"{_repo_base()}/contents/{path}"

    # If a file with the same name already exists for this event, overwrite it
    existing_sha = None
    r = requests.get(url, headers=_headers())
    if r.status_code == 200:
        existing_sha = r.json()["sha"]

    content = base64.b64encode(file_bytes).decode()
    payload = {"message": f"Upload attachment '{safe_name}' (event {event_id})", "content": content}
    if existing_sha:
        payload["sha"] = existing_sha

    r = requests.put(url, headers=_headers(), json=payload)
    r.raise_for_status()
    data = r.json()

    return {
        "id":   datetime.now().strftime("%Y%m%d%H%M%S%f"),
        "name": safe_name,
        "path": path,
        "sha":  data["content"]["sha"],
        "size": len(file_bytes),
    }


def get_attachment_content(path: str) -> bytes:
    """Downloads the raw bytes of an attachment given its repo path."""
    url = f"{_repo_base()}/contents/{path}"
    r   = requests.get(url, headers=_headers())
    r.raise_for_status()
    return base64.b64decode(r.json()["content"])


def delete_attachment(event_id: str, attachment: dict):
    """Deletes a single attachment file from the repo."""
    path = attachment.get("path")
    if not path:
        return
    url = f"{_repo_base()}/contents/{path}"
    r   = requests.get(url, headers=_headers())
    if r.status_code == 404:
        return
    r.raise_for_status()
    sha     = r.json()["sha"]
    payload = {"message": f"Delete attachment '{attachment.get('name')}' (event {event_id})", "sha": sha}
    r = requests.delete(url, headers=_headers(), json=payload)
    r.raise_for_status()
