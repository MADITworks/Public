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

Design notes (v3 — decoupled from client creation):
- Quotes no longer creates clients or contacts. A client/contact must
  already exist (created beforehand in the Clients module). This step is a
  pure SELECTOR over existing data — there is no "New company..." /
  "New contact..." option anymore.
- The only place this file touches anything outside Quotes is
  clients_lookup.load_clients_readonly(), which is READ-ONLY. Nothing here
  writes to clients.json, and nothing here imports quotes_repo or any
  Clients-module code.
- No per-field "Save" buttons. Proposal Title and Date are plain widgets you
  fill in freely; the client/contact pickers are read-only selects.
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

import streamlit as st

try:
    # Caso normal: clients_lookup.py está en la MISMA carpeta que este
    # archivo (client_contact_step.py) — p.ej. dentro de tools/.
    import clients_lookup
except ModuleNotFoundError:
    # Fallback si este archivo se importó como tools.client_contact_step
    # (ver quotes.py) y clients_lookup.py vive dentro del paquete tools/.
    from tools import clients_lookup


# Draft widget keys used inside the Client & Contact form. Nothing here is
# canonical — they only get copied into `quote_client` / `quote_contact` /
# etc. when "Verify & Continue" succeeds.
CLIENT_FORM_WIDGET_KEYS = [
    "cc_company_widget",
    "cc_contact_widget",
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
    """Renderiza Company / Contact / Title / Mobile / Email como selectores
    de SOLO LECTURA sobre clientes/contactos ya existentes. No hay forma de
    crear un cliente o contacto nuevo desde aquí — eso se hace en el módulo
    Clientes. La validación real ocurre toda junta cuando se pulsa
    'Verify & Continue'."""

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
                "⚠️ No clients found. Create the client in the **Clients** module first, "
                "then come back here."
            )
            return "", ""

        # ── Company (solo existentes) ────────────────────────────────────────
        company_options = sorted(clients_db.keys())
        prior_client = st.session_state.get("quote_client", "")
        default_idx = company_options.index(prior_client) if prior_client in company_options else 0

        st.selectbox("🏢 Company", company_options, index=default_idx, key="cc_company_widget")
        company_value = st.session_state["cc_company_widget"]

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        # If the selected company changed since the last render, the contact
        # list it feeds is stale — clear the contact widgets so the person
        # doesn't accidentally submit a contact from a different company.
        # This is a plain top-to-bottom comparison, no on_change callback
        # involved, so there's nothing that can read a not-yet-created
        # widget key.
        if st.session_state.get("_cc_last_seen_company") != company_value:
            st.session_state.pop("cc_contact_widget", None)
            st.session_state.pop("cc_contact_title", None)
            st.session_state.pop("cc_contact_mobile", None)
            st.session_state.pop("cc_contact_email", None)
            st.session_state["_cc_last_seen_company"] = company_value

        # ── Contact Name (solo existentes) ───────────────────────────────────
        contacts_list = clients_db.get(company_value, [])
        contact_names = [c.get("contact", "") for c in contacts_list if c.get("contact")]
        prior_contact = st.session_state.get("quote_contact", "")
        default_c_idx = contact_names.index(prior_contact) if prior_contact in contact_names else 0

        cc1, cc2 = st.columns(2)
        with cc1:
            if not contact_names:
                st.selectbox("👤 Contact Name", ["—"], index=0,
                             key="cc_contact_widget", disabled=True)
                st.caption(
                    "This client has no contacts yet — add one in the **Clients** module."
                )
                contact_value = ""
            else:
                st.selectbox("👤 Contact Name", contact_names, index=default_c_idx, key="cc_contact_widget")
                contact_value = st.session_state["cc_contact_widget"]

        # Title/Mobile/Email vienen del registro del cliente y se muestran
        # de solo lectura: son datos que pertenecen al módulo Clientes, no
        # algo que Quotes deba poder sobreescribir.
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
    """Punto de entrada público: renderiza el Paso 1 completo (selector de
    cliente/contacto existentes + Proposal Title/Date + botón único
    'Verify & Continue' con validación)."""

    st.markdown("### 📝 Step 1 — Client &amp; Contact")
    st.caption("Pick an existing client and contact, then verify to move on to the quote.")

    if "clients_db" not in st.session_state:
        try:
            st.session_state["clients_db"] = clients_lookup.load_clients_readonly()
        except Exception as e:
            st.session_state["clients_db"] = {}
            st.warning(f"Could not load clients: {e}")

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
        errors.append("Company is required — create it in the Clients module first.")
    if not contact_value:
        errors.append("Contact name is required — add one in the Clients module first.")

    email = st.session_state.get("cc_contact_email", "").strip()
    if not email:
        errors.append("The selected contact has no email on file — fix it in the Clients module.")

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
