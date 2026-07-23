import uuid
from datetime import date, datetime

import streamlit as st

from tools.clients.clients_repo import load_clients_db
from tools.calendars import calendars_repo as calendar_repo


FREE_TEXT_LABEL = "✏️ Other / not registered..."
MONTH_NAMES     = ["January", "February", "March", "April", "May", "June",
                   "July", "August", "September", "October", "November", "December"]
WEEKDAY_LABELS  = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]


def _gen_id() -> str:
    return uuid.uuid4().hex[:10]


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month_first = date(year + 1, 1, 1)
    else:
        next_month_first = date(year, month + 1, 1)
    return (next_month_first - date(year, month, 1)).days


def _first_weekday(year: int, month: int) -> int:
    """Monday=0 ... Sunday=6 (same as date.weekday())."""
    return date(year, month, 1).weekday()


def _subtract_months(d: date, months: int) -> date:
    month = d.month - months
    year  = d.year
    while month <= 0:
        month += 12
        year  -= 1
    last_day = _days_in_month(year, month)
    day      = min(d.day, last_day)
    return d.replace(year=year, month=month, day=day)


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%d/%m/%Y").date()


def _fmt_date(d: date) -> str:
    return d.strftime("%d/%m/%Y")


# ── Flash messages ─────────────────────────────────────────────────────────────
def _flash(msg: str, kind: str = "success"):
    st.session_state["_cal_flash"] = (kind, msg)


def _show_flash():
    flash = st.session_state.pop("_cal_flash", None)
    if flash:
        kind, msg = flash
        getattr(st, kind)(msg)


# ── Session cache of events ──────────────────────────────────────────────────────
def _load_events() -> list:
    if "cal_events_cache" not in st.session_state:
        try:
            st.session_state["cal_events_cache"] = calendar_repo.load_events()
        except Exception as e:
            st.session_state["cal_events_cache"] = []
            st.error(f"❌ Error loading calendar events: {e}")
    return st.session_state["cal_events_cache"]


def _refresh_events():
    st.session_state.pop("cal_events_cache", None)


def _build_day_index(events: list) -> dict:
    """Returns {'DD/MM/YYYY': {'events': [...], 'reminders': [(event, reminder), ...]}}"""
    idx = {}
    for e in events:
        idx.setdefault(e["date"], {"events": [], "reminders": []})["events"].append(e)
        for rem in e.get("reminders", []):
            if rem.get("done"):
                continue
            idx.setdefault(rem["date"], {"events": [], "reminders": []})["reminders"].append((e, rem))
    return idx


def _due_reminders(events: list, today: date) -> list:
    due = []
    for e in events:
        for rem in e.get("reminders", []):
            if rem.get("done"):
                continue
            try:
                rdate = _parse_date(rem["date"])
            except (ValueError, TypeError):
                continue
            if rdate <= today:
                due.append((e, rem))
    due.sort(key=lambda pair: pair[1]["date"])
    return due


# ── Editing state (create / edit event) ─────────────────────────────────────────
def _new_editing(default_date: date) -> dict:
    return {
        "id":             None,
        "form_key":       0,
        "event_name":     "",
        "date":           default_date,
        "client_choice":  "",
        "contact_choice": "",
        "notes":          "",
        "reminders": [
            {"id": _gen_id(), "date": _subtract_months(default_date, 2), "auto": True, "done": False}
        ],
        "attachments": [],
    }


def _editing_from_event(event: dict) -> dict:
    reminders = []
    for r in event.get("reminders", []):
        try:
            rdate = _parse_date(r["date"])
        except (ValueError, TypeError):
            rdate = _parse_date(event["date"])
        reminders.append({
            "id": r.get("id", _gen_id()), "date": rdate,
            "auto": False, "done": r.get("done", False),
        })
    if not reminders:
        reminders = [{
            "id": _gen_id(), "date": _subtract_months(_parse_date(event["date"]), 2),
            "auto": False, "done": False,
        }]
    return {
        "id":             event["id"],
        "form_key":       0,
        "event_name":     event.get("event_name", ""),
        "date":           _parse_date(event["date"]),
        "client_choice":  event.get("client", ""),
        "contact_choice": event.get("contact", ""),
        "notes":          event.get("notes", ""),
        "reminders":      reminders,
        "attachments":    list(event.get("attachments", [])),
    }


def _start_add_event(default_date: date):
    st.session_state["cal_editing"] = _new_editing(default_date)


def _start_edit_event(event: dict):
    st.session_state["cal_editing"] = _editing_from_event(event)


def _cancel_editing():
    st.session_state.pop("cal_editing", None)


# ── Unified upcoming list (events + reminders together, sorted by date) ───────
def _upcoming_items(events: list, today: date, limit: int = 8) -> list:
    """Returns a list of dicts, one per upcoming item (event OR reminder),
    sorted by date ascending (soonest first), capped at `limit` items.
    Each dict: {"date": date, "kind": "event"|"reminder", "event": e, "reminder": rem|None}
    """
    items = []
    for e in events:
        try:
            d = _parse_date(e.get("date", ""))
        except (ValueError, TypeError):
            d = None
        if d and d >= today:
            items.append({"date": d, "kind": "event", "event": e, "reminder": None})

        for rem in e.get("reminders", []):
            if rem.get("done"):
                continue
            try:
                rd = _parse_date(rem.get("date", ""))
            except (ValueError, TypeError):
                continue
            if rd >= today:
                items.append({"date": rd, "kind": "reminder", "event": e, "reminder": rem})

    items.sort(key=lambda it: it["date"])
    return items[:limit]


def _render_next_event(events: list):
    st.divider()
    today = date.today()

    st.markdown("### ⏭️ Next upcoming events & reminders")

    upcoming = _upcoming_items(events, today)
    if not upcoming:
        st.caption("No upcoming events or reminders.")
        return

    for item in upcoming:
        item_date = item["date"]
        e         = item["event"]
        rem       = item["reminder"]
        is_event  = item["kind"] == "event"

        days_until = (item_date - today).days
        if days_until == 0:
            when_label = "Today"
        elif days_until == 1:
            when_label = "Tomorrow"
        else:
            when_label = f"in {days_until} days"

        with st.container(border=True):
            c1, c2, c3 = st.columns([4.5, 1, 1])
            with c1:
                tag = "📅 Event" if is_event else "🔔 Reminder"
                st.markdown(f"**{tag} · {e.get('event_name', '')}**")
                if is_event:
                    st.caption(f"📅 {e.get('date', '')}  ·  {when_label}")
                else:
                    st.caption(
                        f"🔔 {rem.get('date', '')}  ·  {when_label}"
                        f"  ·  event on {e.get('date', '')}"
                    )
                sub = []
                if e.get("client"):
                    sub.append(f"🏢 {e['client']}")
                if e.get("contact"):
                    sub.append(f"👤 {e['contact']}")
                if sub:
                    st.caption(" · ".join(sub))
                if is_event and e.get("notes"):
                    st.caption(e["notes"])
            with c2:
                edit_key = f"cal_edit_upcoming_{'event' if is_event else 'reminder'}_{e['id']}_{rem['id'] if rem else ''}"
                if st.button("✏️ Edit", key=edit_key, use_container_width=True):
                    _start_edit_event(e)
                    st.rerun()
            with c3:
                goto_date = e["date"] if is_event else rem["date"]
                goto_key  = f"cal_goto_upcoming_{'event' if is_event else 'reminder'}_{e['id']}_{rem['id'] if rem else ''}"
                if st.button("📆 View day", key=goto_key, use_container_width=True):
                    st.session_state["cal_selected_date"] = goto_date
                    st.rerun()


# ── Pending reminders banner ─────────────────────────────────────────────────────
def _render_reminder_banner(events: list):
    today = date.today()
    due   = _due_reminders(events, today)
    if not due:
        return

    with st.container(border=True):
        st.markdown("#### 🔔 Pending reminders")
        for e, rem in due:
            is_overdue = _parse_date(rem["date"]) < today
            tag   = "🔴 Overdue" if is_overdue else "🟡 Today"
            label = f"{tag} · **{e.get('event_name', '')}** — event on {e.get('date', '')}"
            if e.get("client"):
                label += f" · {e.get('client')}"
            col_a, col_b, col_c = st.columns([4, 1, 1])
            with col_a:
                st.write(label)
            with col_b:
                if st.button("✏️ Edit", key=f"cal_edit_banner_{rem['id']}"):
                    _start_edit_event(e)
                    st.rerun()
            with col_c:
                if st.button("Dismiss", key=f"cal_dismiss_{rem['id']}"):
                    try:
                        calendar_repo.mark_reminder_done(e["id"], rem["id"])
                        _refresh_events()
                        st.rerun()
                    except Exception as ex:
                        st.error(f"❌ Error: {ex}")


# ── Attachments section (inside the event form) ─────────────────────────────────
def _render_attachments_section(editing: dict, fk: int):
    st.markdown("**📎 Attachments**")

    if not editing.get("attachments"):
        st.caption("No files attached yet.")

    for att in list(editing.get("attachments", [])):
        a1, a2, a3 = st.columns([3, 1, 0.6])
        with a1:
            size_kb = (att.get("size") or 0) / 1024
            st.write(f"📄 {att.get('name', '')}  ·  {size_kb:.1f} KB")
        with a2:
            cache_key = f"_cal_att_bytes_{att.get('path')}"
            if cache_key not in st.session_state:
                try:
                    st.session_state[cache_key] = calendar_repo.get_attachment_content(att["path"])
                except Exception:
                    st.session_state[cache_key] = None
            data = st.session_state.get(cache_key)
            if data is not None:
                st.download_button(
                    "⬇️", data=data, file_name=att.get("name", "file"),
                    key=f"cal_att_dl_{att.get('path')}_{fk}",
                )
            else:
                st.caption("⚠️ unavailable")
        with a3:
            if st.button("🗑️", key=f"cal_att_del_{att.get('path')}_{fk}"):
                try:
                    if editing["id"]:
                        calendar_repo.delete_attachment(editing["id"], att)
                    editing["attachments"] = [
                        a for a in editing["attachments"] if a.get("path") != att.get("path")
                    ]
                    if editing["id"]:
                        calendar_repo.save_event(
                            event_name=editing["event_name"] or "(untitled)",
                            event_date=_fmt_date(editing["date"]),
                            client=editing["client_choice"],
                            contact=editing["contact_choice"],
                            notes=editing["notes"],
                            reminders=[
                                {"id": r["id"], "date": _fmt_date(r["date"]), "done": r.get("done", False)}
                                for r in editing["reminders"]
                            ],
                            attachments=editing["attachments"],
                            event_id=editing["id"],
                        )
                        _refresh_events()
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Error deleting attachment: {e}")

    uploaded_files = st.file_uploader(
        "Add new files",
        accept_multiple_files=True,
        key=f"cal_att_upload_{fk}",
    )
    return uploaded_files


# ── Event create / edit form ─────────────────────────────────────────────────────
def _render_event_form(clients_db: dict):
    editing = st.session_state.get("cal_editing")
    if editing is None:
        return

    heading = "✏️ EDIT EVENT" if editing["id"] else "➕ NEW EVENT / REMINDER"
    fk = editing["form_key"]

    with st.container(border=True):
        st.markdown(
            f"<div style='font-size:0.95rem;font-weight:700;color:#1a2a3a;"
            f"letter-spacing:.02em;margin-bottom:10px;'>{heading}</div>",
            unsafe_allow_html=True,
        )

        editing["event_name"] = st.text_input(
            "📌 Event name", value=editing["event_name"], key=f"cal_name_{fk}"
        )

        new_date = st.date_input("📅 Event date", value=editing["date"], key=f"cal_date_{fk}")
        if new_date != editing["date"]:
            editing["date"] = new_date
            for rem in editing["reminders"]:
                if rem.get("auto"):
                    rem["date"] = _subtract_months(new_date, 2)
            editing["form_key"] += 1
            st.rerun()

        col_c1, col_c2 = st.columns(2)
        with col_c1:
            client_options = sorted(clients_db.keys()) + [FREE_TEXT_LABEL]
            c_idx = (
                client_options.index(editing["client_choice"])
                if editing["client_choice"] in clients_db
                else len(client_options) - 1
            )
            client_sel = st.selectbox("🏢 Client", client_options, index=c_idx, key=f"cal_client_{fk}")
            if client_sel == FREE_TEXT_LABEL:
                client_val = st.text_input(
                    "Client name (not registered)",
                    value=editing["client_choice"] if editing["client_choice"] not in clients_db else "",
                    key=f"cal_client_free_{fk}",
                )
            else:
                client_val = client_sel

        with col_c2:
            contacts_list   = clients_db.get(client_val, [])
            contact_names   = [c.get("contact", "") for c in contacts_list if c.get("contact")]
            contact_options = contact_names + [FREE_TEXT_LABEL]
            ct_idx = (
                contact_options.index(editing["contact_choice"])
                if editing["contact_choice"] in contact_names
                else len(contact_options) - 1
            )
            contact_sel = st.selectbox(
                "👤 Contact", contact_options, index=ct_idx, key=f"cal_contact_{fk}_{client_val}"
            )
            if contact_sel == FREE_TEXT_LABEL:
                contact_val = st.text_input(
                    "Contact name (not registered)",
                    value=editing["contact_choice"] if editing["contact_choice"] not in contact_names else "",
                    key=f"cal_contact_free_{fk}_{client_val}",
                )
            else:
                contact_val = contact_sel

        editing["notes"] = st.text_area("📝 Notes (optional)", value=editing["notes"], key=f"cal_notes_{fk}")

        st.markdown("**⏰ Reminders**")
        for i, rem in enumerate(list(editing["reminders"])):
            rcol1, rcol2, rcol3 = st.columns([3, 1, 0.6])
            rem_key = f"cal_rem_{rem['id']}_{fk}"
            with rcol1:
                rnew = st.date_input(f"Reminder {i + 1}", value=rem["date"], key=rem_key)
            if rnew != rem["date"]:
                rem["date"] = rnew
                rem["auto"] = False
            with rcol2:
                if rem.get("done"):
                    st.caption("✅ done")
            with rcol3:
                st.write("")
                if st.button("🗑️", key=f"cal_rem_del_{rem['id']}_{fk}"):
                    editing["reminders"] = [r for r in editing["reminders"] if r["id"] != rem["id"]]
                    st.rerun()

        if st.button("➕ Add another reminder", key=f"cal_rem_add_{fk}"):
            editing["reminders"].append({
                "id": _gen_id(), "date": _subtract_months(editing["date"], 2),
                "auto": False, "done": False,
            })
            st.rerun()

        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

        uploaded_files = _render_attachments_section(editing, fk)

        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
        can_save = bool(editing["event_name"].strip())
        if not can_save:
            st.warning("Event name is required.")

        b1, b2, b3, _ = st.columns([1.2, 1.2, 1.2, 3])
        with b1:
            if st.button("💾 Save", type="primary", disabled=not can_save, key=f"cal_save_{fk}"):
                reminders_out = [
                    {"id": r["id"], "date": _fmt_date(r["date"]), "done": r.get("done", False)}
                    for r in editing["reminders"]
                ]
                try:
                    eid = editing["id"] or _gen_id()

                    new_attachments = list(editing.get("attachments", []))
                    if uploaded_files:
                        for f in uploaded_files:
                            meta = calendar_repo.upload_attachment(eid, f.name, f.getvalue())
                            new_attachments.append(meta)

                    calendar_repo.save_event(
                        event_name=editing["event_name"].strip(),
                        event_date=_fmt_date(editing["date"]),
                        client=client_val.strip(),
                        contact=contact_val.strip(),
                        notes=editing["notes"].strip(),
                        reminders=reminders_out,
                        attachments=new_attachments,
                        event_id=eid,
                    )
                    _flash(f"✅ Event saved: {editing['event_name'].strip()}")
                    _refresh_events()
                    st.session_state["cal_selected_date"] = _fmt_date(editing["date"])
                    _cancel_editing()
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Error saving event: {e}")

        with b2:
            if st.button("✖ Cancel", key=f"cal_cancel_{fk}"):
                _cancel_editing()
                st.rerun()

        if editing["id"]:
            with b3:
                if st.button("🗑️ Delete event", key=f"cal_delete_{fk}"):
                    try:
                        calendar_repo.delete_event(editing["id"])
                        _flash("✅ Event deleted")
                        _refresh_events()
                        _cancel_editing()
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Error deleting event: {e}")


# ── Month grid ────────────────────────────────────────────────────────────────────
def _render_week(cells: list, year: int, month: int, day_index: dict, selected_date_str: str, today: date):
    cols = st.columns(7)
    for col, d in zip(cols, cells):
        with col:
            if d == "":
                st.write("")
                continue
            date_str = f"{d:02d}/{month:02d}/{year}"
            info  = day_index.get(date_str)
            badge = ""
            if info:
                has_ev = bool(info["events"])
                has_rm = bool(info["reminders"])
                if has_ev and has_rm:
                    badge = "🔴"
                elif has_ev:
                    badge = "🔵"
                elif has_rm:
                    badge = "🟡"
            is_today  = (year, month, d) == (today.year, today.month, today.day)
            label     = f"{'•' if is_today else ''}{d}{badge}"
            btn_type  = "primary" if date_str == selected_date_str else "secondary"
            if st.button(label, key=f"cal_day_{year}_{month}_{d}", use_container_width=True, type=btn_type):
                st.session_state["cal_selected_date"] = date_str
                st.rerun()


def _render_month(year: int, month: int, day_index: dict, selected_date_str: str, today: date):
    st.markdown(
        f"<div style='text-align:center;font-weight:700;color:#1a2a3a;"
        f"font-size:0.75rem;margin-bottom:2px;'>{MONTH_NAMES[month - 1]} {year}</div>",
        unsafe_allow_html=True,
    )
    header_cols = st.columns(7)
    for c, wd in zip(header_cols, WEEKDAY_LABELS):
        with c:
            st.markdown(
                f"<div style='text-align:center;font-size:0.6rem;color:#888;'>{wd}</div>",
                unsafe_allow_html=True,
            )

    st.markdown(
        """
        <style>
          div[data-testid="stButton"] button {
              padding: 0.15rem 0.2rem !important;
              min-height: 1.6rem !important;
              font-size: 0.68rem !important;
              line-height: 1 !important;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )

    first_weekday = _first_weekday(year, month)  # Monday=0
    days_in_month = _days_in_month(year, month)
    week_cells = [""] * first_weekday
    for d in range(1, days_in_month + 1):
        week_cells.append(d)
        if len(week_cells) == 7:
            _render_week(week_cells, year, month, day_index, selected_date_str, today)
            week_cells = []
    if week_cells:
        week_cells += [""] * (7 - len(week_cells))
        _render_week(week_cells, year, month, day_index, selected_date_str, today)


# ── Navigation across 2-month blocks ─────────────────────────────────────────────
def _shift_block(delta_months: int):
    y = st.session_state["cal_start_year"]
    m = st.session_state["cal_start_month"] + delta_months
    while m > 12:
        m -= 12
        y += 1
    while m < 1:
        m += 12
        y -= 1
    st.session_state["cal_start_year"]  = y
    st.session_state["cal_start_month"] = m


def _render_nav():
    if "cal_start_year" not in st.session_state:
        st.session_state["cal_start_year"]  = date.today().year
        st.session_state["cal_start_month"] = date.today().month

    nav1, nav2, nav3, nav4, nav5, _ = st.columns([1, 1, 1, 1.4, 1.4, 2.6])
    with nav1:
        if st.button("← Previous", use_container_width=True):
            _shift_block(-2)
            st.rerun()
    with nav2:
        if st.button("Next →", use_container_width=True):
            _shift_block(2)
            st.rerun()
    with nav3:
        if st.button("📍 Today", use_container_width=True):
            st.session_state["cal_start_year"]  = date.today().year
            st.session_state["cal_start_month"] = date.today().month
            st.session_state["cal_selected_date"] = _fmt_date(date.today())
            st.rerun()
    with nav4:
        years = list(range(date.today().year - 5, date.today().year + 6))
        y_idx = years.index(st.session_state["cal_start_year"]) if st.session_state["cal_start_year"] in years else years.index(date.today().year)
        sel_year = st.selectbox("Start year", years, index=y_idx, key="cal_year_select")
        if sel_year != st.session_state["cal_start_year"]:
            st.session_state["cal_start_year"] = sel_year
            st.rerun()
    with nav5:
        m_idx = st.session_state["cal_start_month"] - 1
        sel_month_name = st.selectbox("Start month", MONTH_NAMES, index=m_idx, key="cal_month_select")
        new_month_num = MONTH_NAMES.index(sel_month_name) + 1
        if new_month_num != st.session_state["cal_start_month"]:
            st.session_state["cal_start_month"] = new_month_num
            st.rerun()


# ── Selected day detail ──────────────────────────────────────────────────────────
def _render_day_detail(events: list, selected_date_str: str):
    st.divider()

    head_col, add_col = st.columns([5, 1.4])
    with head_col:
        st.markdown(f"### 📆 {selected_date_str}")
    with add_col:
        if st.button("➕ Add event", use_container_width=True):
            try:
                sel_date_obj = _parse_date(selected_date_str)
            except (ValueError, TypeError):
                sel_date_obj = date.today()
            _start_add_event(sel_date_obj)
            st.rerun()

    day_events = [e for e in events if e.get("date") == selected_date_str]
    day_reminders = []
    for e in events:
        if e.get("date") == selected_date_str:
            continue
        for rem in e.get("reminders", []):
            if rem.get("date") == selected_date_str and not rem.get("done"):
                day_reminders.append((e, rem))

    if not day_events and not day_reminders:
        st.caption("No events or reminders for this day.")

    for e in day_events:
        with st.container(border=True):
            c1, c2, c3 = st.columns([4, 0.7, 0.7])
            with c1:
                st.markdown(f"**{e.get('event_name', '')}**")
                sub = []
                if e.get("client"):
                    sub.append(f"🏢 {e['client']}")
                if e.get("contact"):
                    sub.append(f"👤 {e['contact']}")
                if sub:
                    st.caption(" · ".join(sub))
                if e.get("notes"):
                    st.caption(e["notes"])
                if e.get("reminders"):
                    rem_txt = ", ".join(
                        r["date"] + (" ✅" if r.get("done") else "") for r in e["reminders"]
                    )
                    st.caption(f"⏰ Reminders: {rem_txt}")
                if e.get("attachments"):
                    for att in e["attachments"]:
                        cache_key = f"_cal_att_bytes_{att.get('path')}"
                        if cache_key not in st.session_state:
                            try:
                                st.session_state[cache_key] = calendar_repo.get_attachment_content(att["path"])
                            except Exception:
                                st.session_state[cache_key] = None
                        data = st.session_state.get(cache_key)
                        if data is not None:
                            st.download_button(
                                f"📎 {att.get('name', 'file')}",
                                data=data,
                                file_name=att.get("name", "file"),
                                key=f"cal_view_att_dl_{e['id']}_{att.get('path')}",
                            )
            with c2:
                if st.button("✏️", key=f"cal_edit_{e['id']}", use_container_width=True):
                    _start_edit_event(e)
                    st.rerun()
            with c3:
                if st.button("🗑️", key=f"cal_del_{e['id']}", use_container_width=True):
                    try:
                        calendar_repo.delete_event(e["id"])
                        _flash("✅ Event deleted")
                        _refresh_events()
                        st.rerun()
                    except Exception as ex:
                        st.error(f"❌ Error deleting: {ex}")

    for e, rem in day_reminders:
        st.info(
            f"⏰ Reminder for **{e.get('event_name', '')}** (event on {e.get('date', '')})"
            + (f" · {e.get('client')}" if e.get("client") else "")
        )


# ── Main page ─────────────────────────────────────────────────────────────────────
def show():
    st.title("🗓️ CALENDAR")
    _show_flash()

    events     = _load_events()
    clients_db = load_clients_db()

    _render_reminder_banner(events)

    if "cal_selected_date" not in st.session_state:
        st.session_state["cal_selected_date"] = _fmt_date(date.today())

    _render_nav()
    st.divider()

    day_index   = _build_day_index(events)
    today       = date.today()
    start_year  = st.session_state["cal_start_year"]
    start_month = st.session_state["cal_start_month"]

    months_to_show = []
    y, m = start_year, start_month
    for _ in range(2):
        months_to_show.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1

    for row_start in range(0, len(months_to_show), 2):
        row_months = months_to_show[row_start:row_start + 2]
        if len(row_months) == 2:
            _, col_left, col_gap, col_right, _ = st.columns([1, 2.6, 0.3, 2.6, 1])
            with col_left:
                _render_month(*row_months[0], day_index, st.session_state["cal_selected_date"], today)
            with col_right:
                _render_month(*row_months[1], day_index, st.session_state["cal_selected_date"], today)
        else:
            _, center_col, _ = st.columns([1, 2, 1])
            with center_col:
                _render_month(*row_months[0], day_index, st.session_state["cal_selected_date"], today)

        if row_start + 2 < len(months_to_show):
            st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
            st.divider()

    _render_day_detail(events, st.session_state["cal_selected_date"])
    _render_event_form(clients_db)
    _render_next_event(events)
