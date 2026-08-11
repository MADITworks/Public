import json
import base64
import requests
import streamlit as st
from datetime import datetime


# ── GitHub config (same pattern as calendars_repo.py) ───────────────────────────
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
KANBAN_PATH      = f"{BASE_PATH}/Kanban/data.json"
ATTACHMENTS_PATH = f"{BASE_PATH}/Kanban/attachments"


# ── Read / save the whole dataset (users + boards) ──────────────────────────────
def _get_data_raw() -> tuple[dict, str | None]:
    url = f"{_repo_base()}/contents/{KANBAN_PATH}"
    r   = requests.get(url, headers=_headers())
    if r.status_code == 404:
        return {"users": [], "boards": []}, None
    r.raise_for_status()
    content = base64.b64decode(r.json()["content"]).decode()
    sha     = r.json()["sha"]
    data    = json.loads(content)
    data.setdefault("users", [])
    data.setdefault("boards", [])
    return data, sha


def _save_data_raw(data: dict, sha: str | None, message: str):
    url     = f"{_repo_base()}/contents/{KANBAN_PATH}"
    content = base64.b64encode(
        json.dumps(data, indent=2, ensure_ascii=False).encode()
    ).decode()
    payload = {"message": message, "content": content}
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=_headers(), json=payload)
    r.raise_for_status()


def load_data() -> dict:
    """Returns {'users': [...], 'boards': [...]} with boards each containing lists+cards."""
    data, _ = _get_data_raw()
    return data


def _gen_id() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S%f")


# ── Users ─────────────────────────────────────────────────────────────────────
def save_user(name: str, color: str = "#4A90D9", user_id: str | None = None) -> dict:
    data, sha = _get_data_raw()
    uid      = user_id or _gen_id()
    existing = next((u for u in data["users"] if u["id"] == uid), None)
    record   = {"id": uid, "name": name, "color": color}
    data["users"] = [u for u in data["users"] if u["id"] != uid]
    data["users"].append(record)
    _save_data_raw(data, sha, f"{'Update' if existing else 'Add'} user '{name}'")
    return record


def delete_user(user_id: str):
    data, sha = _get_data_raw()
    data["users"] = [u for u in data["users"] if u["id"] != user_id]
    for b in data["boards"]:
        for c in b.get("cards", []):
            if user_id in c.get("assignees", []):
                c["assignees"] = [a for a in c["assignees"] if a != user_id]
    _save_data_raw(data, sha, f"Delete user {user_id}")


# ── Boards ────────────────────────────────────────────────────────────────────
def save_board(name: str, description: str = "", board_id: str | None = None) -> dict:
    data, sha = _get_data_raw()
    bid      = board_id or _gen_id()
    existing = next((b for b in data["boards"] if b["id"] == bid), None)
    if existing:
        existing["name"]        = name
        existing["description"] = description
        record = existing
    else:
        record = {"id": bid, "name": name, "description": description, "lists": [], "cards": []}
        data["boards"].append(record)
    _save_data_raw(data, sha, f"{'Update' if existing else 'Add'} board '{name}'")
    return record


def delete_board(board_id: str):
    data, sha = _get_data_raw()
    target = next((b for b in data["boards"] if b["id"] == board_id), None)
    data["boards"] = [b for b in data["boards"] if b["id"] != board_id]
    _save_data_raw(data, sha, f"Delete board {board_id}")
    if target:
        for c in target.get("cards", []):
            for att in c.get("attachments", []):
                try:
                    delete_attachment(c["id"], att)
                except Exception:
                    pass


# ── Lists ─────────────────────────────────────────────────────────────────────
def save_list(board_id: str, name: str, list_id: str | None = None) -> str:
    data, sha = _get_data_raw()
    board = next((b for b in data["boards"] if b["id"] == board_id), None)
    if not board:
        raise ValueError("Board not found")
    board.setdefault("lists", [])
    lid      = list_id or _gen_id()
    existing = next((l for l in board["lists"] if l["id"] == lid), None)
    if existing:
        existing["name"] = name
    else:
        board["lists"].append({"id": lid, "name": name, "position": len(board["lists"])})
    _save_data_raw(data, sha, f"{'Update' if existing else 'Add'} list '{name}' (board {board_id})")
    return lid


def delete_list(board_id: str, list_id: str):
    data, sha = _get_data_raw()
    board = next((b for b in data["boards"] if b["id"] == board_id), None)
    if not board:
        return
    board["lists"] = [l for l in board.get("lists", []) if l["id"] != list_id]
    cards_to_remove = [c for c in board.get("cards", []) if c.get("list_id") == list_id]
    board["cards"]  = [c for c in board.get("cards", []) if c.get("list_id") != list_id]
    _save_data_raw(data, sha, f"Delete list {list_id} (board {board_id})")
    for c in cards_to_remove:
        for att in c.get("attachments", []):
            try:
                delete_attachment(c["id"], att)
            except Exception:
                pass


def reorder_lists(board_id: str, ordered_list_ids: list[str]):
    data, sha = _get_data_raw()
    board = next((b for b in data["boards"] if b["id"] == board_id), None)
    if not board:
        return
    for pos, lid in enumerate(ordered_list_ids):
        for l in board["lists"]:
            if l["id"] == lid:
                l["position"] = pos
    _save_data_raw(data, sha, f"Reorder lists (board {board_id})")


# ── Cards ─────────────────────────────────────────────────────────────────────
def save_card(
    board_id:    str,
    list_id:     str,
    title:       str,
    description: str = "",
    assignees:   list[str] | None = None,
    labels:      list[str] | None = None,
    due_date:    str = "",
    attachments: list[dict] | None = None,
    card_id:     str | None = None,
) -> dict:
    data, sha = _get_data_raw()
    board = next((b for b in data["boards"] if b["id"] == board_id), None)
    if not board:
        raise ValueError("Board not found")
    board.setdefault("cards", [])
    cid      = card_id or _gen_id()
    existing = next((c for c in board["cards"] if c["id"] == cid), None)

    record = {
        "id":          cid,
        "board_id":    board_id,
        "list_id":     list_id,
        "title":       title,
        "description": description,
        "assignees":   assignees if assignees is not None else (existing.get("assignees", []) if existing else []),
        "labels":      labels if labels is not None else (existing.get("labels", []) if existing else []),
        "due_date":    due_date,
        "attachments": attachments if attachments is not None
                        else (existing.get("attachments", []) if existing else []),
        "created_at":  existing.get("created_at") if existing else datetime.now().strftime("%d/%m/%Y %H:%M"),
        "position":    existing.get("position", len(board["cards"])) if existing else len(board["cards"]),
    }
    board["cards"] = [c for c in board["cards"] if c["id"] != cid]
    board["cards"].append(record)
    _save_data_raw(data, sha, f"{'Update' if existing else 'Add'} card '{title}' (board {board_id})")
    return record


def move_card(card_id: str, from_board_id: str, to_board_id: str, to_list_id: str):
    """Moves a card to another list, optionally on a different board."""
    data, sha = _get_data_raw()
    src_board = next((b for b in data["boards"] if b["id"] == from_board_id), None)
    if not src_board:
        return
    card = next((c for c in src_board.get("cards", []) if c["id"] == card_id), None)
    if not card:
        return

    src_board["cards"] = [c for c in src_board["cards"] if c["id"] != card_id]
    card["list_id"]  = to_list_id
    card["board_id"] = to_board_id

    if to_board_id == from_board_id:
        src_board["cards"].append(card)
    else:
        dst_board = next((b for b in data["boards"] if b["id"] == to_board_id), None)
        if not dst_board:
            card["board_id"] = from_board_id
            src_board["cards"].append(card)
        else:
            dst_board.setdefault("cards", [])
            dst_board["cards"].append(card)

    _save_data_raw(data, sha, f"Move card {card_id} -> board {to_board_id} / list {to_list_id}")


def delete_card(board_id: str, card_id: str):
    data, sha = _get_data_raw()
    board = next((b for b in data["boards"] if b["id"] == board_id), None)
    if not board:
        return
    target = next((c for c in board.get("cards", []) if c["id"] == card_id), None)
    board["cards"] = [c for c in board.get("cards", []) if c["id"] != card_id]
    _save_data_raw(data, sha, f"Delete card {card_id} (board {board_id})")
    if target:
        for att in target.get("attachments", []):
            try:
                delete_attachment(card_id, att)
            except Exception:
                pass


# ── Attachments ──────────────────────────────────────────────────────────────────
def upload_attachment(card_id: str, filename: str, file_bytes: bytes) -> dict:
    safe_name = filename.replace("/", "_").replace("\\", "_")
    path      = f"{ATTACHMENTS_PATH}/{card_id}/{safe_name}"
    url       = f"{_repo_base()}/contents/{path}"

    existing_sha = None
    r = requests.get(url, headers=_headers())
    if r.status_code == 200:
        existing_sha = r.json()["sha"]

    content = base64.b64encode(file_bytes).decode()
    payload = {"message": f"Upload attachment '{safe_name}' (card {card_id})", "content": content}
    if existing_sha:
        payload["sha"] = existing_sha

    r = requests.put(url, headers=_headers(), json=payload)
    r.raise_for_status()
    data = r.json()

    return {
        "id":   _gen_id(),
        "name": safe_name,
        "path": path,
        "sha":  data["content"]["sha"],
        "size": len(file_bytes),
    }


def get_attachment_content(path: str) -> bytes:
    url = f"{_repo_base()}/contents/{path}"
    r   = requests.get(url, headers=_headers())
    r.raise_for_status()
    return base64.b64decode(r.json()["content"])


def delete_attachment(card_id: str, attachment: dict):
    path = attachment.get("path")
    if not path:
        return
    url = f"{_repo_base()}/contents/{path}"
    r   = requests.get(url, headers=_headers())
    if r.status_code == 404:
        return
    r.raise_for_status()
    sha     = r.json()["sha"]
    payload = {"message": f"Delete attachment '{attachment.get('name')}' (card {card_id})", "sha": sha}
    r = requests.delete(url, headers=_headers(), json=payload)
    r.raise_for_status()
