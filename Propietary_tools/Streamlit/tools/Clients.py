import streamlit as st
import html as _html
from tools import quotes_repo


NEW_COMPANY_LABEL = "➕ New company..."
NEW_CONTACT_LABEL = "➕ New contact..."

FORM_KEYS = [
    "client_form_company", "client_form_contact", "client_form_title",
    "client_form_mobile", "client_form_email",
    "client_form_original_company", "client_form_original_contact",
]
FORM_WIDGET_KEYS = ["client_company_select", "client_contact_select"]


# ── Data helpers ─────────────────────────────────────────────────────────────
def _load_db(force: bool = False) -> dict:
    if force or "clients_db_page" not in st.session_state:
        try:
            st.session_state["clients_db_page"] = quotes_repo.load_clients_db()
        except Exception as e:
            st.session_state["clients_db_page"] = {}
            st.error(f"❌ Error loading clients database: {e}")
    return st.session_state["clients_db_page"]


def _refresh_db():
    st.session_state.pop("clients_db_page", None)


# ── Form state helpers ────────────────────────────────────────────────────────
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
            st.session_state["client_form_contact"]          = ""
            st.session_state["client_form_title"]            = ""
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
                    st.session_state["client_form_contact"]           = choice
                    st.session_state["client_form_title"]             = match.get("title", "")  if match else ""
                    st.session_state["client_form_mobile"]            = match.get("mobile", "") if match else ""
                    st.session_state["client_form_email"]             = match.get("email", "")  if match else ""
                    st.session_state["client_form_original_contact"]  = choice
                    st.session_state["client_form_original_company"]  = st.session_state.get("client_form_company", "")
                else:
                    st.session_state["client_form_contact"]           = ""
                    st.session_state["client_form_title"]             = ""
                    st.session_state["client_form_mobile"]            = ""
                    st.session_state["client_form_email"]             = ""
                    st.session_state["client_form_original_contact"]  = ""

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
        if st.button("💾 Save", type="primary", disabled=not can_save, use_container_width=True):
            try:
                quotes_repo.create_client_company(company_val)
                quotes_repo.update_client_contact(
                    client=company_val,
                    old_contact=st.session_state.get("client_form_original_contact", ""),
                    new_contact=st.session_state.get("client_form_contact", ""),
                    email=st.session_state.get("client_form_email", ""),
                    title=st.session_state.get("client_form_title", ""),
                    mobile=st.session_state.get("client_form_mobile", ""),
                )
                # Si el contacto se movió de empresa (edición cambió el
                # dropdown de Company), lo quitamos de la empresa original.
                original_company = st.session_state.get("client_form_original_company", "")
                original_contact = st.session_state.get("client_form_original_contact", "")
                if original_contact and original_company and original_company != company_val:
                    quotes_repo.delete_client_contact(original_company, original_contact)

                st.success(f"✅ Saved — {company_val}"
                           + (f" / {st.session_state.get('client_form_contact')}" if st.session_state.get("client_form_contact") else ""))
                _refresh_db()
                _reset_form()
                st.rerun()
            except Exception as e:
                st.error(f"❌ Error saving: {e}")

    with btn2:
        if is_editing:
            if st.button("🗑️ Delete Contact", use_container_width=True):
                try:
                    quotes_repo.delete_client_contact(
                        st.session_state.get("client_form_original_company", ""),
                        st.session_state.get("client_form_original_contact", ""),
                    )
                    st.success("✅ Contact deleted")
                    _refresh_db()
                    _reset_form()
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Error deleting contact: {e}")

    with btn3:
        if is_editing or company_val:
            if st.button("✖ Cancel", use_container_width=True):
                _reset_form()
                st.rerun()


# ── Browse / list ────────────────────────────────────────────────────────────
def _render_table(contacts: list) -> str:
    styles = """
    <style>
      .clients-table { width:100%; border-collapse:collapse; font-size:0.82rem;
                        font-family:'Inter','Segoe UI',sans-serif; }
      .clients-table thead tr { background:#1a2a3a; color:#fff; }
      .clients-table thead th { padding:8px 12px; text-align:left;
                                 font-weight:600; letter-spacing:.03em; }
      .clients-table tbody tr { border-bottom:1px solid #e8e8e8; }
      .clients-table tbody tr:nth-child(even) { background:#f7f9fb; }
      .clients-table tbody td { padding:7px 12px; color:#2c3e50; }
    </style>
    """
    header = "<thead><tr><th>Contact</th><th>Title</th><th>Mobile</th><th>Email</th></tr></thead>"
    rows_html = ""
    for c in contacts:
        rows_html += (
            "<tr>"
            f"<td>{_html.escape(c.get('contact','') or '—')}</td>"
            f"<td>{_html.escape(c.get('title','') or '—')}</td>"
            f"<td>{_html.escape(c.get('mobile','') or '—')}</td>"
            f"<td>{_html.escape(c.get('email','') or '—')}</td>"
            "</tr>"
        )
    if not rows_html:
        rows_html = "<tr><td colspan='4' style='color:#888;'>No contacts yet</td></tr>"
    return styles + f'<table class="clients-table">{header}<tbody>{rows_html}</tbody></table>'


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

    for company in companies:
        contacts = clients_db.get(company, [])
        with st.expander(f"🏢 {company}  ·  {len(contacts)} contact(s)"):
            st.markdown(_render_table(contacts), unsafe_allow_html=True)

            st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

            action_cols = st.columns(max(len(contacts), 1) + 2)

            # botones de editar por contacto
            for i, c in enumerate(contacts):
                with action_cols[i]:
                    if st.button(f"✏️ {c.get('contact','') or '—'}", key=f"edit_{company}_{i}", use_container_width=True):
                        _start_edit(company, c)
                        st.rerun()

            with action_cols[len(contacts)]:
                if st.button("➕ Add contact", key=f"addcontact_{company}", use_container_width=True):
                    _start_new_contact_for(company)
                    st.rerun()

            with action_cols[len(contacts) + 1]:
                confirm_key = f"confirm_delete_company_{company}"
                if st.session_state.get(confirm_key):
                    if st.button("⚠️ Confirm delete", key=f"reallydelete_{company}", type="primary", use_container_width=True):
                        try:
                            quotes_repo.delete_client_company(company)
                            st.success(f"✅ Deleted company {company}")
                            _refresh_db()
                            st.session_state.pop(confirm_key, None)
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ Error deleting company: {e}")
                else:
                    if st.button("🗑️ Delete company", key=f"deletecompany_{company}", use_container_width=True):
                        st.session_state[confirm_key] = True
                        st.rerun()


# ── Main page ──────────────────────────────────────────────────────────────────
def show():
    st.title("👥 CLIENTS")
    st.caption("Manage your client and contact database — shared with the Quotes form.")

    if "client_form_company" not in st.session_state:
        _reset_form()

    clients_db = _load_db()

    _render_form(clients_db)
    st.divider()
    _render_browse(clients_db)
