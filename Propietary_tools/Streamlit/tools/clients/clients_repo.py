"""
Step 1 of the Quotes flow: Client & Contact.

Kept in its own file (instead of quotes.py) so that module doesn't keep
growing. quotes.py imports from here:

    from client_contact_step import (
        CLIENT_FORM_WIDGET_KEYS,
        show_client_step,
        snapshot_client_info,
        apply_confirmed_info,
    )

Design notes (v2 — simplified):
- No per-field "Save" buttons. All fields (Company, Contact Name, Contact
  Title, Mobile, Email, Proposal Title, Date) are plain widgets you fill in
  freely, in any order.
- ONE button at the bottom, "✔ Verify & Continue →", validates everything
  at once and — only if everything is valid — commits the values into the
  canonical `quote_*` keys and advances to the quote step.
- Deliberately avoids `on_change=` callbacks entirely. Those read
  `st.session_state[<widget key>]` from inside a callback, which raises a
  KeyError if that widget didn't get instantiated on the run that triggered
  the callback (e.g. it was conditionally hidden, or another field's change
  caused a rerun first). Everything here is read straight from
  st.session_state at button-click time in the main script body instead,
  which is always safe.

Public surface:
- CLIENT_FORM_WIDGET_KEYS: list of session_state keys used by the form's
  widgets, that the caller should pop when resetting/loading a quote.
- show_client_step(): renders the whole step and, on a valid "Verify &
  Continue", sets st.session_state["client_step_done"] = True and snapshots
  the confirmed data.
- snapshot_client_info(): freezes the current quote_* fields into
  st.session_state["confirmed_client_info"]. Exposed so quotes.py can call it
  again after "Save Quote" to keep the snapshot in sync.
- apply_confirmed_info(info): used by quotes.py's _load_saved_quote() to mark
  a saved quote as already having a confirmed client/contact step.
"""

import re
import streamlit as st


NEW_COMPANY_LABEL = "➕ New company..."
NEW_CONTACT_LABEL = "➕ New contact..."

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Draft widget keys used inside the Client & Contact form. Nothing here is
# canonical — they only get copied into `quote_client` / `quote_contact` /
# etc. when "Verify & Continue" succeeds.
CLIENT_FORM_WIDGET_KEYS = [
    "cc_company_widget", "cc_company_new_name",
    "cc_contact_widget", "cc_contact_new_name",
    "cc_contact_title", "cc_contact_mobile", "cc_contact_email",
    "_cc_last_seen_company", "_cc_last_seen_contact",
]


def snapshot_client_info():
    """Congela los datos de cliente/contacto ya validados en un único dict,
    que es la fuente de verdad para todo lo que se muestre más adelante
    (p.ej. 'More details'), en lugar de leer los widgets en vivo."""
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
    """Usado al cargar una quote guardada del historial: ya tiene
    cliente/contacto válidos, así que se marca el Paso 1 como hecho y se
    guarda el snapshot directamente a partir del detalle cargado."""
    st.session_state["confirmed_client_info"] = info
    st.session_state["client_step_done"] = True


def _render_fields(clients_db: dict):
    """Renderiza Company / Contact / Title / Mobile / Email como widgets
    simples, sin botones ni callbacks. Cualquier orden de relleno es válido;
    la validación real ocurre toda junta cuando se pulsa 'Verify & Continue'."""

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

        # ── Company ───────────────────────────────────────────────────────────
        company_options = sorted(clients_db.keys()) + [NEW_COMPANY_LABEL]
        prior_client = st.session_state.get("quote_client", "")
        default_idx = company_options.index(prior_client) if prior_client in clients_db else len(company_options) - 1

        st.selectbox("🏢 Company", company_options, index=default_idx, key="cc_company_widget")
        company_choice = st.session_state["cc_company_widget"]

        if company_choice == NEW_COMPANY_LABEL:
            st.text_input(
                "New company name",
                key="cc_company_new_name",
                value=st.session_state.get(
                    "cc_company_new_name",
                    prior_client if prior_client not in clients_db else "",
                ),
            )
            company_value = st.session_state.get("cc_company_new_name", "").strip()
        else:
            company_value = company_choice

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        # If the selected/typed company changed since the last render, the
        # contact list it feeds is stale — clear the contact widgets so the
        # person doesn't accidentally submit a contact from a different
        # company. This is a plain top-to-bottom comparison, no on_change
        # callback involved, so there's nothing that can read a
        # not-yet-created widget key.
        if st.session_state.get("_cc_last_seen_company") != company_value:
            st.session_state.pop("cc_contact_widget", None)
            st.session_state.pop("cc_contact_new_name", None)
            st.session_state.pop("cc_contact_title", None)
            st.session_state.pop("cc_contact_mobile", None)
            st.session_state.pop("cc_contact_email", None)
            st.session_state["_cc_last_seen_company"] = company_value

        # ── Contact Name / Contact Title ─────────────────────────────────────
        contacts_list   = clients_db.get(company_value, [])
        contact_names   = [c.get("contact", "") for c in contacts_list if c.get("contact")]
        contact_options = contact_names + [NEW_CONTACT_LABEL]
        prior_contact   = st.session_state.get("quote_contact", "")
        default_c_idx   = contact_options.index(prior_contact) if prior_contact in contact_names else len(contact_options) - 1

        cc1, cc2 = st.columns(2)
        with cc1:
            if not company_value:
                st.selectbox("👤 Contact Name", [NEW_CONTACT_LABEL], index=0,
                             key="cc_contact_widget", disabled=True)
                st.caption("Fill in the company first.")
                contact_choice = NEW_CONTACT_LABEL
            else:
                st.selectbox("👤 Contact Name", contact_options, index=default_c_idx, key="cc_contact_widget")
                contact_choice = st.session_state["cc_contact_widget"]
                if contact_choice == NEW_CONTACT_LABEL:
                    st.text_input(
                        "New contact name",
                        key="cc_contact_new_name",
                        value=st.session_state.get(
                            "cc_contact_new_name",
                            prior_contact if prior_contact not in contact_names else "",
                        ),
                    )

        contact_value = (
            st.session_state.get("cc_contact_new_name", "").strip()
            if contact_choice == NEW_CONTACT_LABEL else contact_choice
        )

        # If an existing contact was picked and it changed since the last
        # render, pre-fill Title/Mobile/Email from the clients DB. The
        # person can still overwrite them freely afterwards.
        matched = next((c for c in contacts_list if c.get("contact") == contact_choice), None) \
            if contact_choice != NEW_CONTACT_LABEL else None

        if st.session_state.get("_cc_last_seen_contact") != contact_value:
            st.session_state["_cc_last_seen_contact"] = contact_value
            if matched:
                st.session_state["cc_contact_title"]  = matched.get("title", "")
                st.session_state["cc_contact_mobile"] = matched.get("mobile", "")
                st.session_state["cc_contact_email"]  = matched.get("email", "")
            elif contact_choice == NEW_CONTACT_LABEL:
                st.session_state.pop("cc_contact_title", None)
                st.session_state.pop("cc_contact_mobile", None)
                st.session_state.pop("cc_contact_email", None)

        with cc2:
            st.text_input("💼 Contact Title", key="cc_contact_title")

        # ── Mobile Phone / Email ─────────────────────────────────────────────
        cc3, cc4 = st.columns(2)
        with cc3:
            st.text_input("📱 Mobile Phone", key="cc_contact_mobile")
        with cc4:
            st.text_input("✉️ Email", key="cc_contact_email")

    return company_value, contact_value


def show_client_step():
    """Punto de entrada público: renderiza el Paso 1 completo (formulario +
    Proposal Title/Date + botón único 'Verify & Continue' con validación)."""
    from tools.quotes import quotes_repo

    st.markdown("### 📝 Step 1 — Client &amp; Contact")
    st.caption("Fill in everything, then verify to move on to the quote.")

    if "clients_db" not in st.session_state:
        try:
            st.session_state["clients_db"] = quotes_repo.load_clients_db()
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

    # ── Validate everything at once ──────────────────────────────────────────
    errors = []
    if not company_value:
        errors.append("Company is required.")
    if not contact_value:
        errors.append("Contact name is required.")

    email = st.session_state.get("cc_contact_email", "").strip()
    if not email:
        errors.append("Contact email is required.")
    elif not _EMAIL_RE.match(email):
        errors.append("Contact email doesn't look like a valid email address.")

    proposal_title = st.session_state.get("quote_title", "").strip()
    if not proposal_title:
        errors.append("Proposal title is required.")

    if errors:
        for e in errors:
            st.error(f"⚠️ {e}")
        return

    # ── Commit into the canonical quote_* keys ──────────────────────────────
    # (quote_title is not reassigned here: it's already the live widget key
    # for the Proposal Title input above, so its value is already correct —
    # writing to it again would raise a StreamlitAPIException, since a
    # widget's session_state key can't be reassigned after that widget has
    # already been instantiated in the same run.)
    st.session_state["quote_client"]         = company_value
    st.session_state["quote_contact"]        = contact_value
    st.session_state["quote_contact_title"]  = st.session_state.get("cc_contact_title", "").strip()
    st.session_state["quote_contact_mobile"] = st.session_state.get("cc_contact_mobile", "").strip()
    st.session_state["quote_email"]          = email

    snapshot_client_info()
    st.session_state["client_step_done"] = True
    st.rerun()
