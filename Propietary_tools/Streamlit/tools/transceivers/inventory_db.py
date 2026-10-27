"""MADIT | Transceivers inventory storage (private GitHub repo).

Autonomous module, same pattern as the Clients tool: it only reads/writes
{BASE_PATH}/Transceivers/inventory.json through the GitHub contents API and knows
nothing about Quotes, Clients or Calendar.

Needs the same Streamlit secrets as the Clients tool:

    [github]
    token = "..."
    owner = "..."
    private_repo = "..."

File schema (v1):

    {
      "version": 1,
      "modules": {
        "<SERIAL>": {
          "serial": "<SERIAL>",
          "status": "In stock",
          "vendor": "", "profile_code": "", "client": "",
          "device": "", "device_type": "", "module_bay": "",
          "location": "", "rack": "", "asset_tag": "", "installed_on": "",
          "description": "", "comments": "", "tags": [], "model": "",
          "flexoptix": { part_number, product, form_factor, order, ... },
          "created": "...", "updated": "...", "updated_by": "..."
        }
      }
    }

Every write is ONE read + ONE commit. If GitHub answers 409/422 (stale sha because
someone saved in between) the change is re-applied on a fresh read, up to 3 times.
The inventory is cached in st.session_state so the page does not call GitHub on every
interaction; use load_inventory(force=True) to re-read it.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import requests
import streamlit as st

# ── Carpeta base dentro del repo (igual que el módulo de Clients) ──────────────
BASE_PATH = "Propietary_tools"
FILE_PATH = f"{BASE_PATH}/Transceivers/inventory.json"

CACHE_KEY = "fx_inventory_cache"
MAX_ATTEMPTS = 3
TIMEOUT = 30

STATUSES = ["In stock", "Reserved", "Delivered to client", "Installed", "Spare", "Faulty / RMA", "Retired"]

# Fields the user edits by hand (everything else comes from the Flexoptix API)
TEXT_FIELDS = (
    "status", "vendor", "profile_code", "client", "device", "device_type", "module_bay",
    "location", "rack", "asset_tag", "installed_on", "description", "comments", "model",
)


class InventoryError(Exception):
    """Problem reading/writing the inventory (configuration, GitHub, conflicts)."""


# ── GitHub plumbing ────────────────────────────────────────────────────────────
def _headers(raw: bool = False) -> dict:
    try:
        token = st.secrets["github"]["token"]
    except Exception as exc:
        raise InventoryError(
            "GitHub secrets not found. The [github] section needs token, owner and private_repo."
        ) from exc
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
    }


def _url() -> str:
    try:
        owner = st.secrets["github"]["owner"]
        repo = st.secrets["github"]["private_repo"]
    except Exception as exc:
        raise InventoryError(
            "GitHub secrets not found. The [github] section needs token, owner and private_repo."
        ) from exc
    return f"https://api.github.com/repos/{owner}/{repo}/contents/{FILE_PATH}"


def _empty() -> dict:
    return {"version": 1, "modules": {}}


def _read() -> tuple[dict, str | None]:
    """Returns (data, sha). A missing file is an empty inventory (sha None)."""
    url = _url()
    r = requests.get(url, headers=_headers(), timeout=TIMEOUT)
    if r.status_code == 404:
        return _empty(), None
    r.raise_for_status()
    meta = r.json()
    sha = meta["sha"]

    content = meta.get("content") or ""
    if content.strip():
        text = base64.b64decode(content).decode("utf-8")
    else:  # files over 1 MB come without inline content: ask for the raw file
        raw = requests.get(url, headers=_headers(raw=True), timeout=60)
        raw.raise_for_status()
        text = raw.text

    data = json.loads(text) if text.strip() else _empty()
    data.setdefault("version", 1)
    data.setdefault("modules", {})
    return data, sha


def _write(data: dict, sha: str | None, message: str) -> None:
    payload = {
        "message": message,
        "content": base64.b64encode(
            json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).decode(),
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(_url(), headers=_headers(), json=payload, timeout=60)
    r.raise_for_status()


def _update(mutator, message: str) -> dict:
    """read → mutator(data) → write, retrying on a stale sha.

    mutator(data) edits data in place and returns False when there is nothing to save.
    """
    last_exc: Exception | None = None
    for _ in range(MAX_ATTEMPTS):
        data, sha = _read()
        if mutator(data) is False:
            st.session_state[CACHE_KEY] = data
            return data
        try:
            _write(data, sha, message)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (409, 422):
                last_exc = exc
                continue
            raise
        st.session_state[CACHE_KEY] = data
        return data
    raise InventoryError(
        "The inventory changed on GitHub while saving and the save could not be completed. "
        "Reload and try again."
    ) from last_exc


# ── Helpers ────────────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _user() -> str:
    return str(st.session_state.get("username", "") or "")


def new_record(serial: str, flexoptix: dict | None = None, **fields) -> dict:
    """A complete module record with defaults; `fields` override the manual fields."""
    now = _now()
    rec = {f: "" for f in TEXT_FIELDS}
    rec.update({
        "serial": serial,
        "status": "In stock",
        "tags": [],
        "flexoptix": dict(flexoptix or {}),
        "created": now,
        "updated": now,
        "updated_by": _user(),
    })
    for key, value in fields.items():
        if key in TEXT_FIELDS or key == "tags":
            rec[key] = value
    return rec


# ── Public API ─────────────────────────────────────────────────────────────────
def load_inventory(force: bool = False) -> dict:
    """Returns {serial: record}. Cached in the session; force=True re-reads GitHub."""
    cached = st.session_state.get(CACHE_KEY)
    if cached is None or force:
        data, _ = _read()
        st.session_state[CACHE_KEY] = data
        cached = data
    return cached["modules"]


def add_modules(records: dict[str, dict]) -> tuple[int, int]:
    """Adds records whose serial is not in the inventory yet (one commit).

    Existing modules are never touched. Returns (added, already_present).
    """
    counts = {"added": 0, "present": 0}

    def mutate(data: dict):
        counts["added"] = counts["present"] = 0
        for serial, rec in records.items():
            if serial in data["modules"]:
                counts["present"] += 1
            else:
                data["modules"][serial] = rec
                counts["added"] += 1
        if not counts["added"]:
            return False

    _update(mutate, f"Add {len(records)} transceiver(s) to inventory")
    return counts["added"], counts["present"]


def save_module(serial: str, changes: dict) -> None:
    """Applies `changes` to one module (one commit) and stamps updated / updated_by."""

    def mutate(data: dict):
        rec = data["modules"].get(serial)
        if rec is None:
            raise InventoryError(f"{serial} is not in the inventory (it may have been deleted).")
        rec.update(changes)
        rec["updated"] = _now()
        rec["updated_by"] = _user()

    _update(mutate, f"Update transceiver {serial}")


def delete_module(serial: str) -> None:
    def mutate(data: dict):
        if serial not in data["modules"]:
            return False
        del data["modules"][serial]

    _update(mutate, f"Delete transceiver {serial}")


def delete_modules(serials) -> int:
    """Deletes several modules in ONE commit. Returns how many were actually removed."""
    serials = list(serials)
    counts = {"deleted": 0}

    def mutate(data: dict):
        counts["deleted"] = 0
        for serial in serials:
            if serial in data["modules"]:
                del data["modules"][serial]
                counts["deleted"] += 1
        if not counts["deleted"]:
            return False

    _update(mutate, f"Delete {len(serials)} transceiver(s) from inventory")
    return counts["deleted"]
