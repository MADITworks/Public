import streamlit as st
from tools.clients import clients_repo


NEW_COMPANY_LABEL = "➕ New company..."
NEW_CONTACT_LABEL = "➕ New contact..."

# "Seed" values: only used to compute the default index the FIRST time a
# widget is rendered after starting an edit / new-contact / reset. Plain
# session_state, never a widget's own key.
SEED_KEYS = [
    "cf_seed_company", "cf_seed_contact",
    "cf_original_company", "cf_original_contact",
]

# Actual widget keys + internal "last seen" trackers. These get dropped
# whenever we want the form to fully re-initialize (start edit, new contact,
# reset), so the widgets pick their defaults up fresh from the SEED_KEYS
# above instead of keeping stale values.
WIDGET_KEYS = [
    "cf_company_select", "cf_company_new",
    "cf_contact_select", "cf_contact_new",
    "cf_title", "cf_mobile", "cf_email",
    "_cf_last_seen_company", "_cf_last_seen_contact",
]


# ── Flash messages ──────────────────────────────────────────────────────────────
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
            st.session_state["clients_db_page"] = clients_repo.load_clients_db()
        except Exception as e:
            st.session_state["clients_db_page"] = {}
            st.error(f"❌ Error loading clients database: {e}")
    return st.session_state["clients_db_page"]


def _refresh_db():
    st.session_state.pop("clients_db_page", None)


# ── Form open/close state ────────────────────────────────────────────────────
def _open_form():
    st.session_state["client_form_open"] = True


def _close_form():
    st.session_state["client_form_open"] = False


# ── Form state helpers ───────────────────────────────────────────────────────
def _reset_form():
    for key in SEED_KEYS + WIDGET_KEYS:
        st.session_state.pop(key, None)
    st.session_state["cf_seed_company"]      = ""
    st.session_state["cf_seed_contact"]      = ""
    st.session_state["cf_original_company"]  = ""
    st.session_state["cf_original_contact"]  = ""


def _handle_add_client_click():
    _reset_form()
    _open_form()


def _handle_cancel_form():
    _reset_form()
    _close_form()


def _start_edit(company: str, contact: dict):
    for key in WIDGET_KEYS:
        st.session_state.pop(key, None)
    st.session_state["cf_seed_company"]     = company
    st.session_state["cf_seed_contact"]     = contact.get("contact", "")
    st.session_state["cf_original_company"] = company
    st.session_state["cf_original_contact"] = contact.get("contact", "")
    _open_form()


def _start_new_contact_for(company: str):
    for key in WIDGET_KEYS:
        st.session_state.pop(key, None)
    st.session_state["cf_seed_company"]     = company
    st.session_state["cf_seed_contact"]     = ""
    st.session_state["cf_original_company"] = ""
    st.session_state["cf_original_contact"] = ""
    _open_form()


# ── Acciones (on_click callbacks de botones) ─────────────────────────────────
def _handle_save():
    company_choice = st.session_state.get("cf_company_select", NEW_COMPANY_LABEL)
    company_val = (
        st.session_state.get("cf_company_new", "").strip()
        if company_choice == NEW_COMPANY_LABEL else company_choice
    )
    if not company_val:
        _flash("Company is required.", "error")
        return

    contact_choice = st.session_state.get("cf_contact_select", NEW_CONTACT_LABEL)
    contact_val = (
        st.session_state.get("cf_contact_new", "").strip()
        if contact_choice == NEW_CONTACT_LABEL else contact_choice
    )

    original_contact = st.session_state.get("cf_original_contact", "").strip()
    original_company = st.session_state.get("cf_original_company", "").strip()

    try:
        clients_repo.create_client_company(company_val)

        effective_old_contact = original_contact
        if not effective_old_contact and contact_val:
            fresh_db = clients_repo.load_clients_db()
            existing = fresh_db.get(company_val, [])
            match = next(
                (c for c in existing if c.get("contact", "").strip().lower() == contact_val.lower()),
                None,
            )
            if match:
                effective_old_contact = match.get("contact", "")

        clients_repo.update_client_contact(
            client=company_val,
            old_contact=effective_old_contact,
            new_contact=contact_val,
            email=st.session_state.get("cf_email", "").strip(),
            title=st.session_state.get("cf_title", "").strip(),
            mobile=st.session_state.get("cf_mobile", "").strip(),
        )

        if original_contact and original_company and original_company != company_val:
            clients_repo.delete_client_contact(original_company, original_contact)

        _flash(f"✅ Saved — {company_val}" + (f" / {contact_val}" if contact_val else ""))
        _refresh_db()
        _reset_form()
        _close_form()
    except Exception as e:
        _flash(f"❌ Error saving: {e}", "error")


def _handle_delete_contact_from_form():
    company = st.session_state.get("cf_original_company", "")
    contact = st.session_state.get("cf_original_contact", "")
    try:
        clients_repo.delete_client_contact(company, contact)
        _flash(f"✅ Contact deleted: {contact}")
        _refresh_db()
        _reset_form()
        _close_form()
    except Exception as e:
        _flash(f"❌ Error deleting contact: {e}", "error")


def _handle_delete_contact_row(company: str, contact_name: str):
    try:
        clients_repo.delete_client_contact(company, contact_name)
        _flash(f"✅ Deleted {contact_name}")
        _refresh_db()
        if (
            st.session_state.get("cf_original_company") == company
            and st.session_state.get("cf_original_contact") == contact_name
        ):
            _reset_form()
            _close_form()
    except Exception as e:
        _flash(f"❌ Error deleting: {e}", "error")


def _ask_delete_company(company: str):
    st.session_state[f"confirm_delete_company_{company}"] = True


def _cancel_delete_company(company: str):
    st.session_state.pop(f"confirm_delete_company_{company}", None)


def _handle_delete_company(company: str):
    try:
        clients_repo.delete_client_company(company)
        _flash(f"✅ Deleted company {company}")
        _refresh_db()
        st.session_state.pop(f"confirm_delete_company_{company}", None)
        if st.session_state.get("cf_original_company") == company:
            _reset_form()
            _close_form()
    except Exception as e:
        _flash(f"❌ Error deleting company: {e}", "error")


# ── Form ───────────────────────────────────────────────────────────────────────
def _render_form(clients_db: dict):
    is_editing = bool(st.session_state.get("cf_original_contact"))
    heading = "✏️ EDIT CONTACT" if is_editing else "➕ NEW CLIENT / CONTACT"

    with st.container(border=True):
        st.markdown(
            f"<div style='font-size:0.95rem;font-weight:700;color:#1a2a3a;"
            f"letter-spacing:.02em;margin-bottom:10px;'>{heading}</div>",
            unsafe_allow_html=True,
        )

        company_options = sorted(clients_db.keys()) + [NEW_COMPANY_LABEL]
        seed_company = st.session_state.get("cf_seed_company", "")
        default_idx = (
            company_options.index(seed_company)
            if seed_company in clients_db
            else len(company_options) - 1
        )

        st.selectbox("🏢 Company", company_options, index=default_idx, key="cf_company_select")
        company_choice = st.session_state["cf_company_select"]

        if company_choice == NEW_COMPANY_LABEL:
            st.text_input(
                "New company name",
                key="cf_company_new",
                value=st.session_state.get(
                    "cf_company_new",
                    seed_company if seed_company not in clients_db else "",
                ),
            )
            company_value = st.session_state.get("cf_company_new", "").strip()
        else:
            company_value = company_choice

        if st.session_state.get("_cf_last_seen_company") != company_value:
            st.session_state.pop("cf_contact_select", None)
            st.session_state.pop("cf_contact_new", None)
            st.session_state.pop("cf_title", None)
            st.session_state.pop("cf_mobile", None)
            st.session_state.pop("cf_email", None)
            st.session_state["_cf_last_seen_company"] = company_value

        st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)

        cc1, cc2 = st.columns(2)
        with cc1:
            contacts_list   = clients_db.get(company_value, [])
            contact_names   = [c.get("contact", "") for c in contacts_list if c.get("contact")]
            contact_options = contact_names + [NEW_CONTACT_LABEL]
            seed_contact    = st.session_state.get("cf_seed_contact", "")
            default_c_idx   = (
                contact_options.index(seed_contact)
                if seed_contact in contact_names
                else len(contact_options) - 1
            )

            if not company_value:
                st.selectbox("👤 Contact Name", [NEW_CONTACT_LABEL], index=0,
                             key="cf_contact_select", disabled=True)
                contact_choice = NEW_CONTACT_LABEL
            else:
                st.selectbox("👤 Contact Name", contact_options, index=default_c_idx, key="cf_contact_select")
                contact_choice = st.session_state["cf_contact_select"]
                if contact_choice == NEW_CONTACT_LABEL:
                    st.text_input(
                        "New contact name",
                        key="cf_contact_new",
                        value=st.session_state.get(
                            "cf_contact_new",
                            seed_contact if seed_contact not in contact_names else "",
                        ),
                    )

        contact_value = (
            st.session_state.get("cf_contact_new", "").strip()
            if contact_choice == NEW_CONTACT_LABEL else contact_choice
        )

        matched = next((c for c in contacts_list if c.get("contact") == contact_choice), None) \
            if contact_choice != NEW_CONTACT_LABEL else None

        if st.session_state.get("_cf_last_seen_contact") != contact_value:
            st.session_state["_cf_last_seen_contact"] = contact_value
            if matched:
                st.session_state["cf_title"]  = matched.get("title", "")
                st.session_state["cf_mobile"] = matched.get("mobile", "")
                st.session_state["cf_email"]  = matched.get("email", "")
            elif contact_choice == NEW_CONTACT_LABEL:
                st.session_state.pop("cf_title", None)
                st.session_state.pop("cf_mobile", None)
                st.session_state.pop("cf_email", None)

        with cc2:
            st.text_input("💼 Contact Title", key="cf_title")

        cc3, cc4 = st.columns(2)
        with cc3:
            st.text_input("📱 Mobile Phone", key="cf_mobile")
        with cc4:
            st.text_input("✉️ Email", key="cf_email")

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    can_save = bool(company_value)
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
        st.button("✖ Cancel", use_container_width=True, on_click=_handle_cancel_form)


# ── Browse / list ────────────────────────────────────────────────────────────
def _render_browse(clients_db: dict):
    st.markdown("### 📚 Client Directory")

    if not clients_db:
        st.info("No clients saved yet — click **➕ Add client** above to add your first one.")
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
        with st.expander(f"🏢 {company}  ·  {len(contacts)} contact(s)", expanded=False):

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
    if "cf_seed_company" not in st.session_state:
        _reset_form()
    if "client_form_open" not in st.session_state:
        st.session_state["client_form_open"] = False

    title_col, add_col = st.columns([5, 1.4])
    with title_col:
        st.title("👥 CLIENTS")
        st.caption("Manage your client and contact database — used by the Quotes form.")
    with add_col:
        st.markdown("<div style='height:22px'></div>", unsafe_allow_html=True)
        st.button(
            "➕ Add client", type="primary", use_container_width=True,
            on_click=_handle_add_client_click,
        )

    _show_flash()

    clients_db = _load_db()

    if st.session_state["client_form_open"]:
        _render_form(clients_db)
        st.divider()

    _render_browse(clients_db)
