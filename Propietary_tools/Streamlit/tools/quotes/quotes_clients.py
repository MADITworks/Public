"""
Step 1 of the Quotes flow: Client & Contact.

Vive dentro de tools/quotes/ (no en tools/clients/) porque es puramente UI
del formulario de Quotes. Solo LEE la base de clientes vía
tools.clients.clients_repo.load_clients_db() — nunca crea ni modifica
clientes/contactos. Si el cliente o contacto que necesitas no existe, hay
que darlo de alta primero en la página CLIENTS.
"""

import re
import streamlit as st


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

CLIENT_FORM_WIDGET_KEYS = [
    "cc_company_widget",
    "cc_contact_widget",
    "cc_contact_title", "cc_contact_mobile", "cc_contact_email",
    "_cc_last_seen_company", "_cc_last_seen_contact",
]


def snapshot_client_info():
    st.session_state["confirmed_client_info"] = {
        "client":         st.session_state.get("quote_client", "").strip(),
        "contact":        st.session_state.get("quote_contact", "").strip(),
        "contact_title":  st.session_state.get("quote_contact_title", "").strip(),
        "contact_mobile": st.session_state.get("quote_contact_mobile", "").strip(),
        "email":          st.session_state.get("quote_email", "").strip(),
        "title":          st.session_state.get("quote_title", "").strip(),
        "date":           st.session_state.get("quote_date_obj"),
    }


def apply_confirmed_info(info: dict):
    st.session_state["confirmed_client_info"] = info
    st.session_state["client_step_done"] = True


def _render_fields(clients_db: dict):
    with st.container(border=True):
        hcol1, hcol2 = st.columns([5, 1.3])
        with hcol1:
            st.markdown(
                "<div style='font-size:0.95rem;font-weight:700;color:#1a2a3a;"
                "letter-spacing:.02em;margin-top:6px;'>🏢 CLIENT &amp; CONTACT</div>",
                unsafe_allow_html=True,
            )
        with hcol2:
            if st.button("🔄 Refresh clients", key="refresh_clients_db", use_container_width=True):
                st.session_state.pop("clients_db", None)
                st.rerun()

        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

        if not clients_db:
            st.warning(
                "⚠️ No clients registered yet. Go to the **Clients** page and add "
                "a company/contact before creating a quote."
            )
            return "", ""

        company_options = sorted(clients_db.keys())
        prior_client = st.session_state.get("quote_client", "")
        default_idx  = company_options.index(prior_client) if prior_client in company_options else 0

        st.selectbox("🏢 Company", company_options, index=default_idx, key="cc_company_widget")
        company_value = st.session_state["cc_company_widget"]

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        if st.session_state.get("_cc_last_seen_company") != company_value:
            st.session_state.pop("cc_contact_widget", None)
            st.session_state.pop("cc_contact_title", None)
            st.session_state.pop("cc_contact_mobile", None)
            st.session_state.pop("cc_contact_email", None)
            st.session_state["_cc_last_seen_company"] = company_value

        contacts_list = clients_db.get(company_value, [])
        contact_names = [c.get("contact", "") for c in contacts_list if c.get("contact")]
        prior_contact = st.session_state.get("quote_contact", "")
        default_c_idx = contact_names.index(prior_contact) if prior_contact in contact_names else 0

        cc1, cc2 = st.columns(2)
        with cc1:
            if not contact_names:
                st.selectbox("👤 Contact Name", ["— No contacts for this company —"],
                             index=0, key="cc_contact_widget", disabled=True)
                st.caption("Add a contact for this company on the Clients page first.")
                contact_value = ""
            else:
                st.selectbox("👤 Contact Name", contact_names, index=default_c_idx, key="cc_contact_widget")
                contact_value = st.session_state["cc_contact_widget"]

        matched = next((c for c in contacts_list if c.get("contact") == contact_value), None)

        if st.session_state.get("_cc_last_seen_contact") != contact_value:
            st.session_state["_cc_last_seen_contact"] = contact_value
            st.session_state["cc_contact_title"]  = matched.get("title", "") if matched else ""
            st.session_state["cc_contact_mobile"] = matched.get("mobile", "") if matched else ""
            st.session_state["cc_contact_email"]  = matched.get("email", "") if matched else ""

        with cc2:
            st.text_input("💼 Contact Title", key="cc_contact_title", disabled=True)

        cc3, cc4 = st.columns(2)
        with cc3:
            st.text_input("📱 Mobile Phone", key="cc_contact_mobile", disabled=True)
        with cc4:
            st.text_input("✉️ Email", key="cc_contact_email", disabled=True)

    return company_value, contact_value


def show_client_step():
    from tools.clients import clients_repo

    st.markdown("### 📝 Step 1 — Client &amp; Contact")
    st.caption("Pick an existing client/contact, then verify to move on to the quote.")

    if "clients_db" not in st.session_state:
        try:
            st.session_state["clients_db"] = clients_repo.load_clients_db()
        except Exception as e:
            st.session_state["clients_db"] = {}
            st.warning(f"Could not load clients database: {e}")

    clients_db = st.session_state["clients_db"]

    company_value, contact_value = _render_fields(clients_db)

    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    pc1, pc2 = st.columns(2)
    with pc1:
        st.text_input("📌 Proposal Title", key="quote_title")
    with pc2:
        st.date_input("📅 Date", key="quote_date_obj", format="DD/MM/YYYY")

    st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

    _, col_btn = st.columns([4, 1.6])
    with col_btn:
        verify_clicked = st.button("✔ Verify & Continue →", type="primary", use_container_width=True)

    if not verify_clicked:
        return

    errors = []
    if not company_value:
        errors.append("Company is required — select an existing client.")
    if not contact_value:
        errors.append("Contact name is required — select an existing contact.")

    email = st.session_state.get("cc_contact_email", "").strip()
    if not email:
        errors.append("Contact email is missing for this contact (fix it on the Clients page).")
    elif not _EMAIL_RE.match(email):
        errors.append("Contact email doesn't look like a valid email address.")

    proposal_title = st.session_state.get("quote_title", "").strip()
    if not proposal_title:
        errors.append("Proposal title is required.")

    if errors:
        for e in errors:
            st.error(f"⚠️ {e}")
        return

    st.session_state["quote_client"]         = company_value
    st.session_state["quote_contact"]        = contact_value
    st.session_state["quote_contact_title"]  = st.session_state.get("cc_contact_title", "").strip()
    st.session_state["quote_contact_mobile"] = st.session_state.get("cc_contact_mobile", "").strip()
    st.session_state["quote_email"]          = email

    snapshot_client_info()
    st.session_state["client_step_done"] = True
    st.rerun()
