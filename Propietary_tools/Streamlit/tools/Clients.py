import streamlit as st
from tools import quotes_repo


NEW_COMPANY_LABEL = "➕ New company..."
NEW_CONTACT_LABEL = "➕ New contact..."

FORM_KEYS = [
    "client_form_company", "client_form_contact", "client_form_title",
    "client_form_mobile", "client_form_email",
    "client_form_original_company", "client_form_original_contact",
]
FORM_WIDGET_KEYS = ["client_company_select", "client_contact_select"]


# ── Flash messages ──────────────────────────────────────────────────────────────
# Streamlit no permite llamar st.success/st.error de forma fiable dentro de un
# on_click callback, así que guardamos el mensaje en session_state y lo
# mostramos al comienzo del siguiente render.
def _flash(msg: str, kind: str = "success"):
    st.session_state["_client_flash"] = (kind, msg)


def _show_flash():
    flash = st.session_state.pop("_client_flash", None)
    if flash:
        kind, msg = flash
        getattr(st, kind)(msg)


# ── Data helpers ─────────────────────────────────────────────────────────────
def _load_db() -> dict:
    if "clients_db_page" not in st.session_state:
        try:
            st.session_state["clients_db_page"] = quotes_repo.load_clients_db()
        except Exception as e:
            st.session_state["clients_db_page"] = {}
            st.error(f"❌ Error loading clients database: {e}")
    return st.session_state["clients_db_page"]


def _refresh_db():
    st.session_state.pop("clients_db_page", None)


# ── Form state helpers (todas se usan SOLO como on_click callbacks) ────────────
def _reset_form():
    for key in FORM_KEYS + FORM_WIDGET_KEYS:
        st.session_state.pop(key, None)
    st.session_state["client_form_company"]            = ""
    st.session_state["client_form_contact"]             = ""
    st.session_state["client_form_title"]               = ""
    st.session_state["client_form_mobile"]              = ""
    st.session_state["client_form_email"]               = ""
    st.session_state["client_form_original_company"]    = ""
    st.session_state["client_form_original_contact"]    = ""


def _start_edit(company: str, contact: dict):
    """Carga el formulario con los datos de un contacto existente para editarlo."""
    for key in FORM_WIDGET_KEYS:
        st.session_state.pop(key, None)
    st.session_state["client_form_company"]         = company
    st.session_state["client_form_contact"]          = contact.get("contact", "")
    st.session_state["client_form_title"]            = contact.get("title", "")
    st.session_state["client_form_mobile"]           = contact.get("mobile", "")
    st.session_state["client_form_email"]            = contact.get("email", "")
    st.session_state["client_form_original_company"] = company
    st.session_state["client_form_original_contact"] = contact.get("contact", "")


def _start_new_contact_for(company: str):
    """Prepara el formulario para agregar un contacto nuevo a una empresa existente."""
    for key in FORM_WIDGET_KEYS:
        st.session_state.pop(key, None)
    st.session_state["client_form_company"]         = company
    st.session_state["client_form_contact"]          = ""
    st.session_state["client_form_title"]            = ""
    st.session_state["client_form_mobile"]           = ""
    st.session_state["client_form_email"]            = ""
    st.session_state["client_form_original_company"] = ""
    st.session_state["client_form_original_contact"] = ""


# ── Acciones (on_click callbacks) ───────────────────────────────────────────────
def _handle_save():
    company_val = st.session_state.get("client_form_company", "").strip()
    if not company_val:
        _flash("Company is required.", "error")
        return

    contact_val       = st.session_state.get("client_form_contact", "").strip()
    original_contact  = st.session_state.get("client_form_original_contact", "").strip()
    original_company  = st.session_state.get("client_form_original_company", "").strip()

    try:
        quotes_repo.create_client_company(company_val)

        # Si estamos "creando" un contacto (old_contact vacío) pero ya existe
        # uno con el mismo nombre en esta empresa, lo tratamos como una
        # actualización en vez de crear un duplicado.
        effective_old_contact = original_contact
        if not effective_old_contact and contact_val:
            fresh_db = quotes_repo.load_clients_db()
            existing = fresh_db.get(company_val, [])
            match = next(
                (c for c in existing if c.get("contact", "").strip().lower() == contact_val.lower()),
                None,
            )
            if match:
                effective_old_contact = match.get("contact", "")

        quotes_repo.update_client_contact(
            client=company_val,
            old_contact=effective_old_contact,
            new_contact=contact_val,
            email=st.session_state.get("client_form_email", ""),
            title=st.session_state.get("client_form_title", ""),
            mobile=st.session_state.get("client_form_mobile", ""),
        )

        # Si el contacto se movió de empresa (se editó y se cambió el
        # dropdown de Company), lo quitamos de la empresa original.
        if original_contact and original_company and original_company != company_val:
            quotes_repo.delete_client_contact(original_company, original_contact)

        _flash(f"✅ Saved — {company_val}" + (f" / {contact_val}" if contact_val else ""))
        _refresh_db()
        _reset_form()
    except Exception as e:
        _flash(f"❌ Error saving: {e}", "error")


def _handle_delete_contact_from_form():
    company = st.session_state.get("client_form_original_company", "")
    contact = st.session_state.get("client_form_original_contact", "")
    try:
        quotes_repo.delete_client_contact(company, contact)
        _flash(f"✅ Contact deleted: {contact}")
        _refresh_db()
        _reset_form()
    except Exception as e:
        _flash(f"❌ Error deleting contact: {e}", "error")


def _handle_delete_contact_row(company: str, contact_name: str):
    try:
        quotes_repo.delete_client_contact(company, contact_name)
        _flash(f"✅ Deleted {contact_name}")
        _refresh_db()
        # si justo ese contacto estaba cargado en el formulario, lo limpiamos
        if (
            st.session_state.get("client_form_original_company") == company
            and st.session_state.get("client_form_original_contact") == contact_name
        ):
            _reset_form()
    except Exception as e:
        _flash(f"❌ Error deleting: {e}", "error")


def _ask_delete_company(company: str):
    st.session_state[f"confirm_delete_company_{company}"] = True


def _cancel_delete_company(company: str):
    st.session_state.pop(f"confirm_delete_company_{company}", None)


def _handle_delete_company(company: str):
    try:
        quotes_repo.delete_client_company(company)
        _flash(f"✅ Deleted company {company}")
        _refresh_db()
        st.session_state.pop(f"confirm_delete_company_{company}", None)
        if st.session_state.get("client_form_company") == company:
            _reset_form()
    except Exception as e:
        _flash(f"❌ Error deleting company: {e}", "error")


# ── Form ───────────────────────────────────────────────────────────────────────
def _render_form(clients_db: dict):
    is_editing = bool(st.session_state.get("client_form_original_contact"))
    heading = "✏️ EDIT CONTACT" if is_editing else "➕ NEW CLIENT / CONTACT"

    with st.container(border=True):
        st.markdown(
            f"<div style='font-size:0.95rem;font-weight:700;color:#1a2a3a;"
            f"letter-spacing:.02em;margin-bottom:10px;'>{heading}</div>",
            unsafe_allow_html=True,
        )

        # ── Company ──────────────────────────────────────────────────────────
        company_options = sorted(clients_db.keys()) + [NEW_COMPANY_LABEL]
        current_company = st.session_state.get("client_form_company", "")
        default_idx = (
            company_options.index(current_company)
            if current_company in clients_db
            else len(company_options) - 1
        )

        def _on_company_change():
            choice = st.session_state["client_company_select"]
            st.session_state.pop("client_contact_select", None)
            if choice != NEW_COMPANY_LABEL:
                st.session_state["client_form_company"] = choice
            else:
                st.session_state["client_form_company"] = ""
            # cambiar de empresa invalida cualquier edición de contacto en curso
            st.session_state["client_form_contact"]           = ""
            st.session_state["client_form_title"]             = ""
            st.session_state["client_form_mobile"]            = ""
            st.session_state["client_form_email"]             = ""
            st.session_state["client_form_original_company"] = ""
            st.session_state["client_form_original_contact"] = ""

        st.selectbox(
            "🏢 Company", company_options, index=default_idx,
            key="client_company_select", on_change=_on_company_change,
        )
        if st.session_state["client_company_select"] == NEW_COMPANY_LABEL:
            st.text_input("New company name", key="client_form_company")

        st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)

        # ── Contact Name / Contact Title ────────────────────────────────────
        cc1, cc2 = st.columns(2)
        with cc1:
            contacts_list   = clients_db.get(st.session_state.get("client_form_company", ""), [])
            contact_names   = [c.get("contact", "") for c in contacts_list if c.get("contact")]
            contact_options = contact_names + [NEW_CONTACT_LABEL]
            current_contact = st.session_state.get("client_form_contact", "")
            default_c_idx = (
                contact_options.index(current_contact)
                if current_contact in contact_names
                else len(contact_options) - 1
            )

            def _on_contact_change():
                choice = st.session_state["client_contact_select"]
                if choice != NEW_CONTACT_LABEL:
                    match = next((c for c in contacts_list if c.get("contact") == choice), None)
                    st.session_state["client_form_contact"]          = choice
                    st.session_state["client_form_title"]            = match.get("title", "")  if match else ""
                    st.session_state["client_form_mobile"]           = match.get("mobile", "") if match else ""
                    st.session_state["client_form_email"]            = match.get("email", "")  if match else ""
                    st.session_state["client_form_original_contact"] = choice
                    st.session_state["client_form_original_company"] = st.session_state.get("client_form_company", "")
                else:
                    st.session_state["client_form_contact"]          = ""
                    st.session_state["client_form_title"]            = ""
                    st.session_state["client_form_mobile"]           = ""
                    st.session_state["client_form_email"]            = ""
                    st.session_state["client_form_original_contact"] = ""

            st.selectbox(
                "👤 Contact Name", contact_options, index=default_c_idx,
                key="client_contact_select", on_change=_on_contact_change,
            )
            if st.session_state["client_contact_select"] == NEW_CONTACT_LABEL:
                st.text_input("New contact name", key="client_form_contact")

        with cc2:
            st.text_input("💼 Contact Title", key="client_form_title")

        # ── Mobile Phone / Email ─────────────────────────────────────────────
        cc3, cc4 = st.columns(2)
        with cc3:
            st.text_input("📱 Mobile Phone", key="client_form_mobile")
        with cc4:
            st.text_input("✉️ Email", key="client_form_email")

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    company_val = st.session_state.get("client_form_company", "").strip()
    can_save    = bool(company_val)
    if not can_save:
        st.warning("Fill in at least **Company** to be able to save.")

    btn1, btn2, btn3, _ = st.columns([1.2, 1.2, 1.2, 3])

    with btn1:
        st.button(
            "💾 Save", type="primary", disabled=not can_save,
            use_container_width=True, on_click=_handle_save,
        )

    with btn2:
        if is_editing:
            st.button(
                "🗑️ Delete Contact", use_container_width=True,
                on_click=_handle_delete_contact_from_form,
            )

    with btn3:
        if is_editing or company_val:
            st.button("✖ Cancel", use_container_width=True, on_click=_reset_form)


# ── Browse / list ────────────────────────────────────────────────────────────
def _render_browse(clients_db: dict):
    st.markdown("### 📚 Client Directory")

    if not clients_db:
        st.info("No clients saved yet — use the form above to add your first one.")
        return

    col_f, _ = st.columns([1, 3])
    with col_f:
        search = st.text_input("🔎 Search company", key="client_search", placeholder="Type to filter...")

    companies = sorted(clients_db.keys(), key=str.lower)
    if search:
        companies = [c for c in companies if search.lower() in c.lower()]

    if not companies:
        st.caption("No companies match your search.")
        return

    row_widths = [1.6, 1.7, 1.3, 2.2, 0.55, 0.55]

    for company in companies:
        contacts = clients_db.get(company, [])
        with st.expander(f"🏢 {company}  ·  {len(contacts)} contact(s)"):

            if contacts:
                hc1, hc2, hc3, hc4, hc5, hc6 = st.columns(row_widths)
                hc1.markdown("**Contact**")
                hc2.markdown("**Title**")
                hc3.markdown("**Mobile**")
                hc4.markdown("**Email**")
                hc5.markdown("")
                hc6.markdown("")

                for i, c in enumerate(contacts):
                    c1, c2, c3, c4, c5, c6 = st.columns(row_widths)
                    c1.write(c.get("contact", "") or "—")
                    c2.write(c.get("title", "") or "—")
                    c3.write(c.get("mobile", "") or "—")
                    c4.write(c.get("email", "") or "—")
                    with c5:
                        st.button(
                            "✏️", key=f"edit_{company}_{i}", use_container_width=True,
                            on_click=_start_edit, args=(company, c),
                        )
                    with c6:
                        st.button(
                            "🗑️", key=f"delcontact_{company}_{i}", use_container_width=True,
                            on_click=_handle_delete_contact_row, args=(company, c.get("contact", "")),
                        )
            else:
                st.caption("No contacts yet for this company.")

            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

            add_col, del_col, _ = st.columns([1.4, 1.6, 3])
            with add_col:
                st.button(
                    "➕ Add contact", key=f"addcontact_{company}", use_container_width=True,
                    on_click=_start_new_contact_for, args=(company,),
                )
            with del_col:
                confirm_key = f"confirm_delete_company_{company}"
                if st.session_state.get(confirm_key):
                    cdc1, cdc2 = st.columns(2)
                    with cdc1:
                        st.button(
                            "⚠️ Confirm", key=f"reallydelete_{company}", type="primary",
                            use_container_width=True, on_click=_handle_delete_company, args=(company,),
                        )
                    with cdc2:
                        st.button(
                            "✖", key=f"canceldelete_{company}", use_container_width=True,
                            on_click=_cancel_delete_company, args=(company,),
                        )
                else:
                    st.button(
                        "🗑️ Delete company", key=f"deletecompany_{company}", use_container_width=True,
                        on_click=_ask_delete_company, args=(company,),
                    )


# ── Main page ──────────────────────────────────────────────────────────────────
def show():
    st.title("👥 CLIENTS")
    st.caption("Manage your client and contact database — shared with the Quotes form.")

    _show_flash()

    if "client_form_company" not in st.session_state:
        _reset_form()

    clients_db = _load_db()

    _render_form(clients_db)
    st.divider()
    _render_browse(clients_db)
