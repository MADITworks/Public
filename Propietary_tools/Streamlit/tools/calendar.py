import uuid
from datetime import date, datetime

import streamlit as st

from tools.quotes_repo import load_clients_db
from tools import calendar_repo


FREE_TEXT_LABEL = "✏️ Otro / no registrado..."
MONTH_NAMES_ES  = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
                   "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
WEEKDAY_LABELS  = ["Lu", "Ma", "Mi", "Ju", "Vi", "Sá", "Do"]


def _gen_id() -> str:
    return uuid.uuid4().hex[:10]


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month_first = date(year + 1, 1, 1)
    else:
        next_month_first = date(year, month + 1, 1)
    return (next_month_first - date(year, month, 1)).days


def _first_weekday(year: int, month: int) -> int:
    """Lunes=0 ... Domingo=6 (igual que date.weekday())."""
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


# ── Flash messages ───────────────────────────────────────────────────────────────
def _flash(msg: str, kind: str = "success"):
    st.session_state["_cal_flash"] = (kind, msg)


def _show_flash():
    flash = st.session_state.pop("_cal_flash", None)
    if flash:
        kind, msg = flash
        getattr(st, kind)(msg)


# ── Cache de eventos en sesión ───────────────────────────────────────────────────
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
    """Devuelve {'DD/MM/YYYY': {'events': [...], 'reminders': [(event, reminder), ...]}}"""
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


# ── Estado de edición (alta / edición de evento) ────────────────────────────────
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
    }


def _start_add_event(default_date: date):
    st.session_state["cal_editing"] = _new_editing(default_date)


def _start_edit_event(event: dict):
    st.session_state["cal_editing"] = _editing_from_event(event)


def _cancel_editing():
    st.session_state.pop("cal_editing", None)


# ── Banner de recordatorios pendientes ───────────────────────────────────────────
def _render_reminder_banner(events: list):
    today = date.today()
    due   = _due_reminders(events, today)
    if not due:
        return

    with st.container(border=True):
        st.markdown("#### 🔔 Recordatorios pendientes")
        for e, rem in due:
            is_overdue = _parse_date(rem["date"]) < today
            tag   = "🔴 Vencido" if is_overdue else "🟡 Hoy"
            label = f"{tag} · **{e.get('event_name', '')}** — evento el {e.get('date', '')}"
            if e.get("client"):
                label += f" · {e.get('client')}"
            col_a, col_b = st.columns([5, 1])
            with col_a:
                st.write(label)
            with col_b:
                if st.button("Descartar", key=f"cal_dismiss_{rem['id']}"):
                    try:
                        calendar_repo.mark_reminder_done(e["id"], rem["id"])
                        _refresh_events()
                        st.rerun()
                    except Exception as ex:
                        st.error(f"❌ Error: {ex}")


# ── Formulario de alta / edición de evento ───────────────────────────────────────
def _render_event_form(clients_db: dict):
    editing = st.session_state.get("cal_editing")
    if editing is None:
        return

    heading = "✏️ EDITAR EVENTO" if editing["id"] else "➕ NUEVO EVENTO / RECORDATORIO"
    fk = editing["form_key"]

    with st.container(border=True):
        st.markdown(
            f"<div style='font-size:0.95rem;font-weight:700;color:#1a2a3a;"
            f"letter-spacing:.02em;margin-bottom:10px;'>{heading}</div>",
            unsafe_allow_html=True,
        )

        editing["event_name"] = st.text_input(
            "📌 Nombre del evento", value=editing["event_name"], key=f"cal_name_{fk}"
        )

        new_date = st.date_input("📅 Fecha del evento", value=editing["date"], key=f"cal_date_{fk}")
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
            client_sel = st.selectbox("🏢 Cliente", client_options, index=c_idx, key=f"cal_client_{fk}")
            if client_sel == FREE_TEXT_LABEL:
                client_val = st.text_input(
                    "Nombre del cliente (no registrado)",
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
                "👤 Contacto", contact_options, index=ct_idx, key=f"cal_contact_{fk}_{client_val}"
            )
            if contact_sel == FREE_TEXT_LABEL:
                contact_val = st.text_input(
                    "Nombre del contacto (no registrado)",
                    value=editing["contact_choice"] if editing["contact_choice"] not in contact_names else "",
                    key=f"cal_contact_free_{fk}_{client_val}",
                )
            else:
                contact_val = contact_sel

        editing["notes"] = st.text_area("📝 Notas (opcional)", value=editing["notes"], key=f"cal_notes_{fk}")

        st.markdown("**⏰ Recordatorios**")
        for i, rem in enumerate(list(editing["reminders"])):
            rcol1, rcol2, rcol3 = st.columns([3, 1, 0.6])
            rem_key = f"cal_rem_{rem['id']}_{fk}"
            with rcol1:
                rnew = st.date_input(f"Recordatorio {i + 1}", value=rem["date"], key=rem_key)
            if rnew != rem["date"]:
                rem["date"] = rnew
                rem["auto"] = False
            with rcol2:
                if rem.get("done"):
                    st.caption("✅ hecho")
            with rcol3:
                st.write("")
                if st.button("🗑️", key=f"cal_rem_del_{rem['id']}_{fk}"):
                    editing["reminders"] = [r for r in editing["reminders"] if r["id"] != rem["id"]]
                    st.rerun()

        if st.button("➕ Añadir otro recordatorio", key=f"cal_rem_add_{fk}"):
            editing["reminders"].append({
                "id": _gen_id(), "date": _subtract_months(editing["date"], 2),
                "auto": False, "done": False,
            })
            st.rerun()

        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
        can_save = bool(editing["event_name"].strip())
        if not can_save:
            st.warning("El nombre del evento es obligatorio.")

        b1, b2, b3, _ = st.columns([1.2, 1.2, 1.2, 3])
        with b1:
            if st.button("💾 Guardar", type="primary", disabled=not can_save, key=f"cal_save_{fk}"):
                reminders_out = [
                    {"id": r["id"], "date": _fmt_date(r["date"]), "done": r.get("done", False)}
                    for r in editing["reminders"]
                ]
                try:
                    calendar_repo.save_event(
                        event_name=editing["event_name"].strip(),
                        event_date=_fmt_date(editing["date"]),
                        client=client_val.strip(),
                        contact=contact_val.strip(),
                        notes=editing["notes"].strip(),
                        reminders=reminders_out,
                        event_id=editing["id"],
                    )
                    _flash(f"✅ Evento guardado: {editing['event_name'].strip()}")
                    _refresh_events()
                    st.session_state["cal_selected_date"] = _fmt_date(editing["date"])
                    _cancel_editing()
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Error guardando el evento: {e}")

        with b2:
            if st.button("✖ Cancelar", key=f"cal_cancel_{fk}"):
                _cancel_editing()
                st.rerun()

        if editing["id"]:
            with b3:
                if st.button("🗑️ Borrar evento", key=f"cal_delete_{fk}"):
                    try:
                        calendar_repo.delete_event(editing["id"])
                        _flash("✅ Evento borrado")
                        _refresh_events()
                        _cancel_editing()
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Error borrando el evento: {e}")


# ── Grid de meses ────────────────────────────────────────────────────────────────
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
        f"font-size:0.85rem;margin-bottom:4px;'>{MONTH_NAMES_ES[month - 1]} {year}</div>",
        unsafe_allow_html=True,
    )
    header_cols = st.columns(7)
    for c, wd in zip(header_cols, WEEKDAY_LABELS):
        with c:
            st.markdown(
                f"<div style='text-align:center;font-size:0.68rem;color:#888;'>{wd}</div>",
                unsafe_allow_html=True,
            )

    first_weekday = _first_weekday(year, month)  # Lunes=0
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


# ── Navegación de bloques de 4 meses ─────────────────────────────────────────────
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

    nav1, nav2, nav3, nav4, _ = st.columns([1, 1, 1.4, 1.4, 3])
    with nav1:
        if st.button("← Anterior", use_container_width=True):
            _shift_block(-4)
            st.rerun()
    with nav2:
        if st.button("Siguiente →", use_container_width=True):
            _shift_block(4)
            st.rerun()
    with nav3:
        years = list(range(date.today().year - 5, date.today().year + 6))
        y_idx = years.index(st.session_state["cal_start_year"]) if st.session_state["cal_start_year"] in years else years.index(date.today().year)
        sel_year = st.selectbox("Año inicio", years, index=y_idx, key="cal_year_select")
        if sel_year != st.session_state["cal_start_year"]:
            st.session_state["cal_start_year"] = sel_year
            st.rerun()
    with nav4:
        m_idx = st.session_state["cal_start_month"] - 1
        sel_month_name = st.selectbox("Mes inicio", MONTH_NAMES_ES, index=m_idx, key="cal_month_select")
        new_month_num = MONTH_NAMES_ES.index(sel_month_name) + 1
        if new_month_num != st.session_state["cal_start_month"]:
            st.session_state["cal_start_month"] = new_month_num
            st.rerun()


# ── Detalle del día seleccionado ─────────────────────────────────────────────────
def _render_day_detail(events: list, selected_date_str: str):
    st.divider()

    head_col, add_col = st.columns([5, 1.4])
    with head_col:
        st.markdown(f"### 📆 {selected_date_str}")
    with add_col:
        if st.button("➕ Añadir evento", use_container_width=True):
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
        st.caption("Sin eventos ni recordatorios este día.")

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
                    st.caption(f"⏰ Recordatorios: {rem_txt}")
            with c2:
                if st.button("✏️", key=f"cal_edit_{e['id']}", use_container_width=True):
                    _start_edit_event(e)
                    st.rerun()
            with c3:
                if st.button("🗑️", key=f"cal_del_{e['id']}", use_container_width=True):
                    try:
                        calendar_repo.delete_event(e["id"])
                        _flash("✅ Evento borrado")
                        _refresh_events()
                        st.rerun()
                    except Exception as ex:
                        st.error(f"❌ Error borrando: {ex}")

    for e, rem in day_reminders:
        st.info(
            f"⏰ Recordatorio de **{e.get('event_name', '')}** (evento el {e.get('date', '')})"
            + (f" · {e.get('client')}" if e.get("client") else "")
        )


# ── Página principal ─────────────────────────────────────────────────────────────
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
    for _ in range(4):
        months_to_show.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1

    month_cols = st.columns(4)
    for col, (yy, mm) in zip(month_cols, months_to_show):
        with col:
            _render_month(yy, mm, day_index, st.session_state["cal_selected_date"], today)

    _render_day_detail(events, st.session_state["cal_selected_date"])
    _render_event_form(clients_db)
