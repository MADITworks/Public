import streamlit as st

from tools.kanban import kanban_repo as kanban_repo

DEFAULT_LISTS = ["Por hacer", "En proceso", "Hecho"]
DEFAULT_COLORS = ["#4A90D9", "#D9534F", "#5CB85C", "#F0AD4E", "#9B59B6", "#1ABC9C"]


# ── Flash messages ─────────────────────────────────────────────────────────────
def _flash(msg: str, kind: str = "success"):
    st.session_state["_kb_flash"] = (kind, msg)


def _show_flash():
    flash = st.session_state.pop("_kb_flash", None)
    if flash:
        kind, msg = flash
        getattr(st, kind)(msg)


# ── Session cache ────────────────────────────────────────────────────────────────
def _load_data() -> dict:
    if "kb_data_cache" not in st.session_state:
        try:
            st.session_state["kb_data_cache"] = kanban_repo.load_data()
        except Exception as e:
            st.session_state["kb_data_cache"] = {"users": [], "boards": []}
            st.error(f"❌ Error loading kanban data: {e}")
    return st.session_state["kb_data_cache"]


def _refresh_data():
    st.session_state.pop("kb_data_cache", None)


def _users_by_id(users: list) -> dict:
    return {u["id"]: u for u in users}


def _sorted_lists(board: dict) -> list:
    return sorted(board.get("lists", []), key=lambda l: l.get("position", 0))


def _cards_in_list(board: dict, list_id: str) -> list:
    cards = [c for c in board.get("cards", []) if c.get("list_id") == list_id]
    return sorted(cards, key=lambda c: c.get("position", 0))


# ── Users management ─────────────────────────────────────────────────────────────
def _render_users_panel(data: dict):
    with st.expander("👥 Users"):
        users = data.get("users", [])
        for u in users:
            c1, c2 = st.columns([4, 1])
            with c1:
                st.markdown(
                    f"<span style='display:inline-block;width:10px;height:10px;border-radius:50%;"
                    f"background:{u.get('color', '#888')};margin-right:6px;'></span>{u.get('name', '')}",
                    unsafe_allow_html=True,
                )
            with c2:
                if st.button("🗑️", key=f"kb_user_del_{u['id']}"):
                    try:
                        kanban_repo.delete_user(u["id"])
                        _refresh_data()
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Error: {e}")

        st.markdown("**➕ Add user**")
        nc1, nc2, nc3 = st.columns([3, 2, 1])
        with nc1:
            new_name = st.text_input("Name", key="kb_new_user_name", label_visibility="collapsed",
                                       placeholder="User name")
        with nc2:
            new_color = st.selectbox("Color", DEFAULT_COLORS, key="kb_new_user_color",
                                       label_visibility="collapsed")
        with nc3:
            if st.button("Add", key="kb_new_user_add", use_container_width=True):
                if new_name.strip():
                    try:
                        kanban_repo.save_user(new_name.strip(), new_color)
                        _refresh_data()
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Error: {e}")
                else:
                    st.warning("Enter a name first.")


# ── Board selector / creation ────────────────────────────────────────────────────
def _render_board_selector(data: dict) -> dict | None:
    boards = data.get("boards", [])
    top1, top2, top3 = st.columns([3, 1.3, 1.3])

    with top1:
        if boards:
            names = [b["name"] for b in boards]
            if "kb_selected_board" not in st.session_state or st.session_state["kb_selected_board"] not in [b["id"] for b in boards]:
                st.session_state["kb_selected_board"] = boards[0]["id"]
            current_id = st.session_state["kb_selected_board"]
            idx = next((i for i, b in enumerate(boards) if b["id"] == current_id), 0)
            sel_name = st.selectbox("📋 Board", names, index=idx, key="kb_board_select")
            sel_board = next(b for b in boards if b["name"] == sel_name)
            st.session_state["kb_selected_board"] = sel_board["id"]
        else:
            st.info("No boards yet — create your first one ➡️")
            sel_board = None

    with top2:
        st.write("")
        if st.button("➕ New board", use_container_width=True):
            st.session_state["kb_new_board_open"] = True

    with top3:
        st.write("")
        if sel_board and st.button("🗑️ Delete board", use_container_width=True):
            try:
                kanban_repo.delete_board(sel_board["id"])
                _flash(f"✅ Board '{sel_board['name']}' deleted")
                _refresh_data()
                st.session_state.pop("kb_selected_board", None)
                st.rerun()
            except Exception as e:
                st.error(f"❌ Error: {e}")

    if st.session_state.get("kb_new_board_open"):
        with st.container(border=True):
            st.markdown("**➕ New board**")
            b_name = st.text_input("Board name", key="kb_new_board_name")
            b_desc = st.text_input("Description (optional)", key="kb_new_board_desc")
            bc1, bc2, _ = st.columns([1, 1, 3])
            with bc1:
                if st.button("Create", key="kb_new_board_create"):
                    if b_name.strip():
                        try:
                            board = kanban_repo.save_board(b_name.strip(), b_desc.strip())
                            for lname in DEFAULT_LISTS:
                                kanban_repo.save_list(board["id"], lname)
                            _flash(f"✅ Board '{b_name.strip()}' created")
                            _refresh_data()
                            st.session_state["kb_selected_board"] = board["id"]
                            st.session_state["kb_new_board_open"] = False
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ Error: {e}")
                    else:
                        st.warning("Enter a board name.")
            with bc2:
                if st.button("Cancel", key="kb_new_board_cancel"):
                    st.session_state["kb_new_board_open"] = False
                    st.rerun()

    return sel_board


# ── Card detail / edit panel ─────────────────────────────────────────────────────
def _start_edit_card(card: dict):
    st.session_state["kb_editing_card"] = dict(card)
    st.session_state["kb_editing_card_fk"] = st.session_state.get("kb_editing_card_fk", 0) + 1


def _start_new_card(board_id: str, list_id: str):
    st.session_state["kb_editing_card"] = {
        "id": None, "board_id": board_id, "list_id": list_id, "title": "",
        "description": "", "assignees": [], "labels": [], "due_date": "", "attachments": [],
    }
    st.session_state["kb_editing_card_fk"] = st.session_state.get("kb_editing_card_fk", 0) + 1


def _cancel_edit_card():
    st.session_state.pop("kb_editing_card", None)


def _render_card_attachments(card: dict, fk: int):
    st.markdown("**📎 Attachments**")
    if not card.get("attachments"):
        st.caption("No files attached yet.")

    for att in list(card.get("attachments", [])):
        a1, a2, a3 = st.columns([3, 1, 0.6])
        with a1:
            size_kb = (att.get("size") or 0) / 1024
            st.write(f"📄 {att.get('name', '')}  ·  {size_kb:.1f} KB")
        with a2:
            cache_key = f"_kb_att_bytes_{att.get('path')}"
            if cache_key not in st.session_state:
                try:
                    st.session_state[cache_key] = kanban_repo.get_attachment_content(att["path"])
                except Exception:
                    st.session_state[cache_key] = None
            data = st.session_state.get(cache_key)
            if data is not None:
                st.download_button("⬇️", data=data, file_name=att.get("name", "file"),
                                    key=f"kb_att_dl_{att.get('path')}_{fk}")
            else:
                st.caption("⚠️ unavailable")
        with a3:
            if st.button("🗑️", key=f"kb_att_del_{att.get('path')}_{fk}"):
                try:
                    if card["id"]:
                        kanban_repo.delete_attachment(card["id"], att)
                    card["attachments"] = [a for a in card["attachments"] if a.get("path") != att.get("path")]
                    if card["id"]:
                        kanban_repo.save_card(
                            board_id=card["board_id"], list_id=card["list_id"], title=card["title"],
                            description=card["description"], assignees=card["assignees"],
                            labels=card["labels"], due_date=card["due_date"],
                            attachments=card["attachments"], card_id=card["id"],
                        )
                        _refresh_data()
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Error deleting attachment: {e}")

    return st.file_uploader("Add new files", accept_multiple_files=True, key=f"kb_att_upload_{fk}")


def _render_card_form(data: dict, board: dict):
    card = st.session_state.get("kb_editing_card")
    if card is None:
        return
    fk = st.session_state.get("kb_editing_card_fk", 0)
    users = data.get("users", [])
    heading = "✏️ EDIT CARD" if card["id"] else "➕ NEW CARD"

    with st.container(border=True):
        st.markdown(
            f"<div style='font-size:0.95rem;font-weight:700;color:#1a2a3a;"
            f"letter-spacing:.02em;margin-bottom:10px;'>{heading}</div>",
            unsafe_allow_html=True,
        )

        card["title"] = st.text_input("📌 Title", value=card["title"], key=f"kb_card_title_{fk}")
        card["description"] = st.text_area("📝 Description", value=card["description"], key=f"kb_card_desc_{fk}")

        c1, c2 = st.columns(2)
        with c1:
            user_names = {u["id"]: u["name"] for u in users}
            sel_ids = st.multiselect(
                "👤 Assignees",
                options=list(user_names.keys()),
                default=[uid for uid in card.get("assignees", []) if uid in user_names],
                format_func=lambda uid: user_names.get(uid, uid),
                key=f"kb_card_assignees_{fk}",
            )
            card["assignees"] = sel_ids
        with c2:
            due_str = st.text_input("📅 Due date (DD/MM/YYYY, optional)",
                                     value=card.get("due_date", ""), key=f"kb_card_due_{fk}")
            card["due_date"] = due_str

        labels_str = st.text_input("🏷️ Labels (comma separated)",
                                    value=", ".join(card.get("labels", [])), key=f"kb_card_labels_{fk}")
        card["labels"] = [l.strip() for l in labels_str.split(",") if l.strip()]

        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

        # Move to another list / board
        st.markdown("**🔀 Move card**")
        mv1, mv2 = st.columns(2)
        boards = data.get("boards", [])
        with mv1:
            board_names = [b["name"] for b in boards]
            cur_board_idx = next((i for i, b in enumerate(boards) if b["id"] == card["board_id"]), 0)
            mv_board_name = st.selectbox("Board", board_names, index=cur_board_idx, key=f"kb_card_mv_board_{fk}")
            mv_board = next(b for b in boards if b["name"] == mv_board_name)
        with mv2:
            mv_lists = _sorted_lists(mv_board)
            mv_list_names = [l["name"] for l in mv_lists]
            cur_list_idx = next((i for i, l in enumerate(mv_lists) if l["id"] == card["list_id"]), 0) \
                if mv_board["id"] == card["board_id"] else 0
            mv_list_name = st.selectbox("List", mv_list_names, index=min(cur_list_idx, len(mv_list_names) - 1) if mv_list_names else 0,
                                          key=f"kb_card_mv_list_{fk}")
            mv_list = next((l for l in mv_lists if l["name"] == mv_list_name), None)

        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
        uploaded_files = _render_card_attachments(card, fk)

        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
        can_save = bool(card["title"].strip())
        if not can_save:
            st.warning("Title is required.")

        b1, b2, b3, _ = st.columns([1.2, 1.2, 1.2, 3])
        with b1:
            if st.button("💾 Save", type="primary", disabled=not can_save, key=f"kb_card_save_{fk}"):
                try:
                    is_new = card["id"] is None
                    cid = card["id"] or kanban_repo._gen_id()

                    new_attachments = list(card.get("attachments", []))
                    if uploaded_files:
                        for f in uploaded_files:
                            meta = kanban_repo.upload_attachment(cid, f.name, f.getvalue())
                            new_attachments.append(meta)

                    target_board_id = mv_board["id"] if mv_board else card["board_id"]
                    target_list_id  = mv_list["id"] if mv_list else card["list_id"]

                    kanban_repo.save_card(
                        board_id=card["board_id"], list_id=card["list_id"],
                        title=card["title"].strip(), description=card["description"].strip(),
                        assignees=card["assignees"], labels=card["labels"], due_date=card["due_date"].strip(),
                        attachments=new_attachments, card_id=cid,
                    )

                    if not is_new and (target_board_id != card["board_id"] or target_list_id != card["list_id"]):
                        kanban_repo.move_card(cid, card["board_id"], target_board_id, target_list_id)
                    elif is_new and (target_board_id != card["board_id"] or target_list_id != card["list_id"]):
                        kanban_repo.move_card(cid, card["board_id"], target_board_id, target_list_id)

                    _flash(f"✅ Card saved: {card['title'].strip()}")
                    _refresh_data()
                    _cancel_edit_card()
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Error saving card: {e}")
        with b2:
            if st.button("✖ Cancel", key=f"kb_card_cancel_{fk}"):
                _cancel_edit_card()
                st.rerun()
        if card["id"]:
            with b3:
                if st.button("🗑️ Delete card", key=f"kb_card_delete_{fk}"):
                    try:
                        kanban_repo.delete_card(card["board_id"], card["id"])
                        _flash("✅ Card deleted")
                        _refresh_data()
                        _cancel_edit_card()
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Error deleting: {e}")


# ── Board board / lists / cards rendering ────────────────────────────────────────
def _render_card_summary(card: dict, users_by_id: dict):
    with st.container(border=True):
        st.markdown(f"**{card.get('title', '')}**")
        if card.get("due_date"):
            st.caption(f"📅 {card['due_date']}")
        if card.get("labels"):
            tags = " ".join(f"`{l}`" for l in card["labels"])
            st.caption(tags)
        if card.get("assignees"):
            names = [users_by_id[a]["name"] for a in card["assignees"] if a in users_by_id]
            if names:
                st.caption("👤 " + ", ".join(names))
        if card.get("attachments"):
            st.caption(f"📎 {len(card['attachments'])} file(s)")
        if st.button("Open", key=f"kb_open_card_{card['id']}", use_container_width=True):
            _start_edit_card(card)
            st.rerun()


def _render_list_column(board: dict, lst: dict, users_by_id: dict):
    with st.container(border=True):
        h1, h2, h3 = st.columns([3, 0.8, 0.8])
        with h1:
            st.markdown(f"#### {lst['name']}")
        with h2:
            if st.button("✏️", key=f"kb_list_edit_{lst['id']}"):
                st.session_state[f"kb_list_rename_{lst['id']}"] = True
        with h3:
            if st.button("🗑️", key=f"kb_list_del_{lst['id']}"):
                try:
                    kanban_repo.delete_list(board["id"], lst["id"])
                    _flash(f"✅ List '{lst['name']}' deleted")
                    _refresh_data()
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Error: {e}")

        if st.session_state.get(f"kb_list_rename_{lst['id']}"):
            new_name = st.text_input("Rename list", value=lst["name"], key=f"kb_list_rename_input_{lst['id']}")
            rc1, rc2 = st.columns(2)
            with rc1:
                if st.button("Save", key=f"kb_list_rename_save_{lst['id']}"):
                    try:
                        kanban_repo.save_list(board["id"], new_name.strip(), list_id=lst["id"])
                        st.session_state.pop(f"kb_list_rename_{lst['id']}", None)
                        _refresh_data()
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Error: {e}")
            with rc2:
                if st.button("Cancel", key=f"kb_list_rename_cancel_{lst['id']}"):
                    st.session_state.pop(f"kb_list_rename_{lst['id']}", None)
                    st.rerun()

        for card in _cards_in_list(board, lst["id"]):
            _render_card_summary(card, users_by_id)

        if st.button("➕ Add card", key=f"kb_add_card_{lst['id']}", use_container_width=True):
            _start_new_card(board["id"], lst["id"])
            st.rerun()


def _render_board(data: dict, board: dict):
    users_by_id = _users_by_id(data.get("users", []))
    lists = _sorted_lists(board)

    if board.get("description"):
        st.caption(board["description"])

    nl1, _ = st.columns([1.5, 5])
    with nl1:
        if st.button("➕ New list", use_container_width=True):
            st.session_state["kb_new_list_open"] = True

    if st.session_state.get("kb_new_list_open"):
        with st.container(border=True):
            nl_name = st.text_input("List name", key="kb_new_list_name")
            c1, c2, _ = st.columns([1, 1, 3])
            with c1:
                if st.button("Create", key="kb_new_list_create"):
                    if nl_name.strip():
                        try:
                            kanban_repo.save_list(board["id"], nl_name.strip())
                            _refresh_data()
                            st.session_state["kb_new_list_open"] = False
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ Error: {e}")
                    else:
                        st.warning("Enter a list name.")
            with c2:
                if st.button("Cancel", key="kb_new_list_cancel"):
                    st.session_state["kb_new_list_open"] = False
                    st.rerun()

    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

    if not lists:
        st.caption("No lists yet — add one to start organizing cards.")
        return

    cols = st.columns(len(lists))
    for col, lst in zip(cols, lists):
        with col:
            _render_list_column(board, lst, users_by_id)


# ── Main page ─────────────────────────────────────────────────────────────────────
def show():
    st.title("🗂️ KANBAN")
    _show_flash()

    data = _load_data()

    _render_users_panel(data)
    st.divider()

    board = _render_board_selector(data)
    st.divider()

    if board:
        # re-fetch the up-to-date board object from cache (in case it changed)
        board = next((b for b in data["boards"] if b["id"] == board["id"]), board)
        _render_board(data, board)
        _render_card_form(data, board)
