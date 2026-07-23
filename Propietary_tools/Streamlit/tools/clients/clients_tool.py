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

# ── Company form: seeds + widget keys (prefijo co_ para no chocar con cf_) ────
CO_SEED_KEYS = ["co_seed_name", "co_original_name"]

CO_WIDGET_KEYS = [
    "co_name_select", "co_name_new",
    "co_abn", "co_industry", "co_phone", "co_website", "co_notes",
    "_co_last_seen_name",
]

ADDR_WIDGET_KEYS = [
    "addr_label", "addr_line1", "addr_line2",
    "addr_city", "addr_state", "addr_zip", "addr_country",
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


def _load_companies() -> dict:
    if "companies_db_page" not in st.session_state:
        try:
            st.session_state["companies_db_page"] = clients_repo.load_companies_db()
        except Exception as e:
            st.session_state["companies_db_page"] = {}
            st.error(f"❌ Error loading companies database: {e}")
    return st.session_state["companies_db_page"]


def _refresh_db():
    st.session_state.pop("clients_db_page", None)
    st.session_state.pop("companies_db_page", None)


# ── Form open/close state (contacts) ─────────────────────────────────────────
def _open_form():
    st.session_state["client_form_open"] = True


def _close_form():
    st.session_state["client_form_open"] = False


# ── Form open/close state (companies) ────────────────────────────────────────
def _open_co_form():
    st.session_state["company_form_open"] = True


def _close_co_form():
    st.session_state["company_form_open"] = False


# ── Form state helpers (contacts) ────────────────────────────────────────────
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


# ── Form state helpers (companies) ───────────────────────────────────────────
def _reset_co_form():
    for key in CO_SEED_KEYS + CO_WIDGET_KEYS:
        st.session_state.pop(key, None)
    st.session_state.pop("co_addresses_editor", None)
    st.session_state["co_seed_name"]     = ""
    st.session_state["co_original_name"] = ""


def _handle_add_company_click():
    _reset_co_form()
    st.session_state["co_addresses_editor"] = []
    _open_co_form()


def _handle_cancel_co_form():
    _reset_co_form()
    _close_co_form()


def _start_edit_company(company: str, info: dict):
    for key in CO_WIDGET_KEYS:
        st.session_state.pop(key, None)
    st.session_state["co_seed_name"]     = company
    st.session_state["co_original_name"] = company
    st.session_state["co_abn"]           = info.get("abn", "")
    st.session_state["co_industry"]      = info.get("industry", "")
    st.session_state["co_phone"]         = info.get("phone", "")
    st.session_state["co_website"]       = info.get("website", "")
    st.session_state["co_notes"]         = info.get("notes", "")
    st.session_state["co_addresses_editor"] = list(info.get("addresses", []))
    _open_co_form()


# ── Acciones (on_click callbacks de botones) — CONTACTS ──────────────────────
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


# ── Acciones — COMPANIES ─────────────────────────────────────────────────────
def _handle_save_company():
    name_choice = st.session_state.get("co_name_select", NEW_COMPANY_LABEL)
    name_val = (
        st.session_state.get("co_name_new", "").strip()
        if name_choice == NEW_COMPANY_LABEL else name_choice
    )
    if not name_val:
        _flash("Company name is required.", "error")
        return

    original_name = st.session_state.get("co_original_name", "").strip()
    addresses_df  = st.session_state.get("co_addresses_table")

    try:
        if original_name and original_name != name_val:
            clients_repo.rename_client_company(original_name, name_val)

        clients_repo.create_client_company(name_val)
        clients_repo.update_company_info(
            client=name_val,
            abn=st.session_state.get("co_abn", "").strip(),
            industry=st.session_state.get("co_industry", "").strip(),
            phone=st.session_state.get("co_phone", "").strip(),
            website=st.session_state.get("co_website", "").strip(),
            notes=st.session_state.get("co_notes", "").strip(),
        )

        if addresses_df is not None:
            addresses_list = addresses_df.fillna("").to_dict("records")
            clients_repo.set_company_addresses(name_val, addresses_list)

        _flash(f"✅ Company saved — {name_val}")
        _refresh_db()
        _reset_co_form()
        _close_co_form()
    except Exception as e:
        _flash(f"❌ Error saving company: {e}", "error")


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
        if st.session_state.get("co_original_name") == company:
            _reset_co_form()
            _close_co_form()
        if st.session_state.get("cf_original_company") == company:
            _reset_form()
            _close_form()
    except Exception as e:
        _flash(f"❌ Error deleting company: {e}", "error")


# ── Acciones — ADDRESSES (fila suelta, fuera del form de edición) ───────────
def _start_add_address(company: str):
    for key in ADDR_WIDGET_KEYS:
        st.session_state.pop(key, None)
    st.session_state["addr_target_company"] = company
    st.session_state["addr_edit_index"]      = None
    st.session_state["address_form_open"]    = True


def _start_edit_address(company: str, index: int, addr: dict):
    st.session_state["addr_label"]   = addr.get("label", "")
    st.session_state["addr_line1"]   = addr.get("line1", "")
    st.session_state["addr_line2"]   = addr.get("line2", "")
    st.session_state["addr_city"]    = addr.get("city", "")
    st.session_state["addr_state"]   = addr.get("state", "")
    st.session_state["addr_zip"]     = addr.get("zip", "")
    st.session_state["addr_country"] = addr.get("country", "")
    st.session_state["addr_target_company"] = company
    st.session_state["addr_edit_index"]      = index
    st.session_state["address_form_open"]    = True


def _handle_cancel_address():
    for key in ADDR_WIDGET_KEYS:
        st.session_state.pop(key, None)
    st.session_state.pop("addr_target_company", None)
    st.session_state.pop("addr_edit_index", None)
    st.session_state["address_form_open"] = False


def _handle_save_address():
    company = st.session_state.get("addr_target_company", "")
    index   = st.session_state.get("addr_edit_index")
    kwargs  = dict(
        label=st.session_state.get("addr_label", ""),
        line1=st.session_state.get("addr_line1", ""),
        line2=st.session_state.get("addr_line2", ""),
        city=st.session_state.get("addr_city", ""),
        state=st.session_state.get("addr_state", ""),
        zip_code=st.session_state.get("addr_zip", ""),
        country=st.session_state.get("addr_country", ""),
    )
    try:
        if index is None:
            clients_repo.add_company_address(company, **kwargs)
        else:
            clients_repo.update_company_address(company, index, **kwargs)
        _flash(f"✅ Address saved for {company}")
        _refresh_db()
        _handle_cancel_address()
    except Exception as e:
        _flash(f"❌ Error saving address: {e}", "error")


def _handle_delete_address(company: str, index: int):
    try:
        clients_repo.delete_company_address(company, index)
        _flash(f"✅ Address deleted from {company}")
        _refresh_db()
    except Exception as e:
        _flash(f"❌ Error deleting address: {e}", "error")


# ── Contact Form ──────────────────────────────────────────────────────────────
def _render_form(clients_db: dict):
    is_editing = bool(st.session_state.get("cf_original_contact"))
    heading = "✏️ EDIT CONTACT" if is_editing else "➕ NEW CONTACT"

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


# ── Company Form ──────────────────────────────────────────────────────────────
def _render_company_form(companies_db: dict):
    is_editing = bool(st.session_state.get("co_original_name"))
    heading = "✏️ EDIT COMPANY" if is_editing else "➕ NEW COMPANY"

    with st.container(border=True):
        st.markdown(
            f"<div style='font-size:0.95rem;font-weight:700;color:#1a2a3a;"
            f"letter-spacing:.02em;margin-bottom:10px;'>{heading}</div>",
            unsafe_allow_html=True,
        )

        if is_editing:
            st.text_input("🏢 Company name", key="co_name_new_edit",
                          value=st.session_state.get("co_seed_name", ""))
            st.session_state["co_name_select"] = NEW_COMPANY_LABEL
            st.session_state["co_name_new"] = st.session_state.get("co_name_new_edit", "")
            name_value = st.session_state["co_name_new"].strip()
        else:
            st.text_input("🏢 Company name", key="co_name_new")
            st.session_state["co_name_select"] = NEW_COMPANY_LABEL
            name_value = st.session_state.get("co_name_new", "").strip()

        st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)

        cc1, cc2 = st.columns(2)
        with cc1:
            st.text_input("🏷️ ABN", key="co_abn")
        with cc2:
            st.text_input("🏭 Industry", key="co_industry")

        cc3, cc4 = st.columns(2)
        with cc3:
            st.text_input("📞 Phone", key="co_phone")
        with cc4:
            st.text_input("🌐 Website", key="co_website")

        st.text_area("📝 Notes", key="co_notes", height=80)

        # ── Gestión de múltiples direcciones/edificios ───────────────────────
        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
        st.markdown("**📍 Addresses** — un edificio por fila, añade tantas como necesites")

        import pandas as pd
        default_addr_cols = ["label", "line1", "line2", "city", "state", "zip", "country"]
        seed_addresses = st.session_state.get("co_addresses_editor", [])
        addr_df = pd.DataFrame(seed_addresses) if seed_addresses else pd.DataFrame(columns=default_addr_cols)
        for col in default_addr_cols:
            if col not in addr_df.columns:
                addr_df[col] = ""
        addr_df = addr_df[default_addr_cols]

        st.data_editor(
            addr_df,
            key="co_addresses_table",
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            column_config={
                "label":   st.column_config.TextColumn("Label (e.g. HQ, Warehouse)", width="medium"),
                "line1":   st.column_config.TextColumn("Address line 1", width="large"),
                "line2":   st.column_config.TextColumn("Address line 2", width="medium"),
                "city":    st.column_config.TextColumn("City", width="small"),
                "state":   st.column_config.TextColumn("State", width="small"),
                "zip":     st.column_config.TextColumn("Zip", width="small"),
                "country": st.column_config.TextColumn("Country", width="small"),
            },
        )

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    can_save = bool(name_value)
    if not can_save:
        st.warning("Fill in **Company name** to be able to save.")

    btn1, btn2, _ = st.columns([1.2, 1.2, 3])
    with btn1:
        st.button(
            "💾 Save", type="primary", disabled=not can_save,
            use_container_width=True, on_click=_handle_save_company,
        )
    with btn2:
        st.button("✖ Cancel", use_container_width=True, on_click=_handle_cancel_co_form)


# ── Address Form (inline, se abre dentro del expander de la empresa) ─────────
def _render_address_form():
    is_editing = st.session_state.get("addr_edit_index") is not None
    heading = "✏️ Edit address" if is_editing else "➕ New address"

    st.markdown(f"**{heading}**")
    ac1, ac2 = st.columns(2)
    with ac1:
        st.text_input("Label (e.g. Billing, Shipping)", key="addr_label")
    with ac2:
        st.text_input("Address line 1", key="addr_line1")

    ac3, ac4 = st.columns(2)
    with ac3:
        st.text_input("Address line 2", key="addr_line2")
    with ac4:
        st.text_input("City", key="addr_city")

    ac5, ac6, ac7 = st.columns(3)
    with ac5:
        st.text_input("State", key="addr_state")
    with ac6:
        st.text_input("Zip / Postcode", key="addr_zip")
    with ac7:
        st.text_input("Country", key="addr_country")

    ab1, ab2, _ = st.columns([1, 1, 3])
    with ab1:
        st.button("💾 Save address", type="primary", use_container_width=True,
                  on_click=_handle_save_address)
    with ab2:
        st.button("✖ Cancel", use_container_width=True, on_click=_handle_cancel_address)


# ── Browse / list — CONTACTS ─────────────────────────────────────────────────
def _render_browse_contacts(clients_db: dict):
    st.markdown("### 📚 Contact Directory")

    if not clients_db:
        st.info("No contacts saved yet — click **➕ Add contact** above to add your first one.")
        return

    all_companies = sorted(clients_db.keys(), key=str.lower)

    col_f1, col_f2 = st.columns([1.4, 1.6])
    with col_f1:
        company_filter = st.selectbox(
            "🏢 Filter by company",
            ["All companies"] + all_companies,
            key="client_company_filter",
        )
    with col_f2:
        search = st.text_input(
            "🔎 Search company",
            key="client_search",
            placeholder="Type a letter or word to filter...",
        )

    companies = all_companies
    if company_filter != "All companies":
        companies = [c for c in companies if c == company_filter]
    if search:
        companies = [c for c in companies if search.lower() in c.lower()]

    if not companies:
        st.caption("No companies match your search.")
        return

    row_widths = [1.6, 1.7, 1.3, 2.2, 0.55, 0.55]

    for company in companies:
        contacts = clients_db.get(company, [])
        with st.expander(
            f"🏢 {company}  ·  {len(contacts)} contact(s)",
            expanded=(company_filter != "All companies"),
        ):

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

            st.button(
                "➕ Add contact", key=f"addcontact_{company}",
                on_click=_start_new_contact_for, args=(company,),
            )


# ── Browse / list — COMPANIES ────────────────────────────────────────────────
def _render_browse_companies(companies_db: dict):
    st.markdown("### 🏢 Company Directory")

    if not companies_db:
        st.info("No companies saved yet — click **➕ Add company** above to add your first one.")
        return

    col_f, _ = st.columns([1, 3])
    with col_f:
        search = st.text_input("🔎 Search company", key="company_search", placeholder="Type to filter...")

    names = sorted(companies_db.keys(), key=str.lower)
    if search:
        names = [n for n in names if search.lower() in n.lower()]

    if not names:
        st.caption("No companies match your search.")
        return

    for company in names:
        info      = companies_db.get(company, {})
        addresses = info.get("addresses", [])

        with st.expander(f"🏢 {company}  ·  {len(addresses)} address(es)", expanded=False):
            ic1, ic2 = st.columns(2)
            with ic1:
                st.write(f"**ABN:** {info.get('abn') or '—'}")
                st.write(f"**Industry:** {info.get('industry') or '—'}")
            with ic2:
                st.write(f"**Phone:** {info.get('phone') or '—'}")
                st.write(f"**Website:** {info.get('website') or '—'}")
            st.write(f"**Notes:** {info.get('notes') or '—'}")

            st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
            st.markdown("**📍 Addresses**")

            if addresses:
                for idx, addr in enumerate(addresses):
                    line = ", ".join(
                        v for v in [addr.get("line1"), addr.get("line2"), addr.get("city"),
                                    addr.get("state"), addr.get("zip"), addr.get("country")]
                        if v
                    )
                    ac1, ac2, ac3 = st.columns([1.4, 4, 1.2])
                    ac1.write(f"**{addr.get('label') or '—'}**")
                    ac2.write(line or "—")
                    with ac3:
                        eb, db = st.columns(2)
                        with eb:
                            st.button("✏️", key=f"editaddr_{company}_{idx}",
                                      use_container_width=True,
                                      on_click=_start_edit_address, args=(company, idx, addr))
                        with db:
                            st.button("🗑️", key=f"deladdr_{company}_{idx}",
                                      use_container_width=True,
                                      on_click=_handle_delete_address, args=(company, idx))
            else:
                st.caption("No addresses yet.")

            if (
                st.session_state.get("address_form_open")
                and st.session_state.get("addr_target_company") == company
            ):
                st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
                _render_address_form()
            else:
                st.button(
                    "➕ Add address", key=f"addaddr_{company}",
                    on_click=_start_add_address, args=(company,),
                )

            st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

            ec1, dc1, _ = st.columns([1.4, 1.6, 3])
            with ec1:
                st.button(
                    "✏️ Edit company", key=f"editcompany_{company}", use_container_width=True,
                    on_click=_start_edit_company, args=(company, info),
                )
            with dc1:
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
    if "co_seed_name" not in st.session_state:
        _reset_co_form()
    if "client_form_open" not in st.session_state:
        st.session_state["client_form_open"] = False
    if "company_form_open" not in st.session_state:
        st.session_state["company_form_open"] = False
    if "clients_view" not in st.session_state:
        st.session_state["clients_view"] = "companies"

    st.title("👥 CLIENTS")
    st.caption("Manage your companies and contacts — used by the Quotes form.")

    _show_flash()

    view_col, add_col = st.columns([5, 1.4])
    with view_col:
        view = st.radio(
            "View",
            ["🏢 Companies", "👤 Contacts"],
            horizontal=True,
            label_visibility="collapsed",
            index=0 if st.session_state["clients_view"] == "companies" else 1,
        )
        st.session_state["clients_view"] = "companies" if view == "🏢 Companies" else "contacts"

    with add_col:
        if st.session_state["clients_view"] == "companies":
            st.button(
                "➕ Add company", type="primary", use_container_width=True,
                on_click=_handle_add_company_click,
            )
        else:
            st.button(
                "➕ Add contact", type="primary", use_container_width=True,
                on_click=_handle_add_client_click,
            )

    st.divider()

    if st.session_state["clients_view"] == "companies":
        companies_db = _load_companies()
        if st.session_state["company_form_open"]:
            _render_company_form(companies_db)
            st.divider()
        _render_browse_companies(companies_db)
    else:
        clients_db = _load_db()
        if st.session_state["client_form_open"]:
            _render_form(clients_db)
            st.divider()
        _render_browse_contacts(clients_db)
