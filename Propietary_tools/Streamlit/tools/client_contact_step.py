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

Public surface:
- CLIENT_FORM_WIDGET_KEYS: list of session_state keys used by the form's
  widgets, that the caller should pop when resetting/loading a quote.
- show_client_step(): renders the whole step (form + Proposal Title/Date +
  "Continue" button with validation). Sets st.session_state["client_step_done"]
  = True and snapshots the confirmed data once validation passes.
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

# Draft widget keys used inside the Client & Contact form. These are
# intentionally SEPARATE from the canonical `quote_*` keys — nothing is
# written to the canonical keys until the person clicks the corresponding
# "✔ Save" button for that field, so there's never any ambiguity about
# what's actually been committed.
CLIENT_FORM_WIDGET_KEYS = [
    "company_select_widget", "company_new_name_draft",
    "contact_select_widget", "contact_new_name_draft",
    "contact_title_draft", "contact_mobile_draft", "contact_email_draft",
    "_company_field_error", "_contact_field_error", "_email_field_error",
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


def validate_client_step() -> list[str]:
    errors = []
    if not st.session_state.get("quote_client", "").strip():
        errors.append("Company is required.")
    if not st.session_state.get("quote_contact", "").strip():
        errors.append("Contact name is required.")
    email = st.session_state.get("quote_email", "").strip()
    if not email:
        errors.append("Contact email is required.")
    elif not _EMAIL_RE.match(email):
        errors.append("Contact email doesn't look like a valid email address.")
    if not st.session_state.get("quote_title", "").strip():
        errors.append("Proposal title is required.")
    return errors


def _render_client_contact_form(clients_db: dict):
    """Renderiza los campos de Company / Contact / Title / Mobile / Email.

    Cada campo tiene su PROPIO botón "✔ Save". Los widgets escriben en una
    clave 'draft' separada; nada llega a las claves canónicas (`quote_client`,
    `quote_contact`, ...) — que son las que lee la validación y el resto de
    la app — hasta que se pulsa el botón de ese campo. Así se elimina
    cualquier ambigüedad sobre si un valor "quedó guardado" o no: debajo de
    cada campo se muestra explícitamente el valor realmente confirmado.
    """

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
        company_options  = sorted(clients_db.keys()) + [NEW_COMPANY_LABEL]
        confirmed_client = st.session_state.get("quote_client", "")
        default_idx = company_options.index(confirmed_client) if confirmed_client in clients_db else len(company_options) - 1

        comp_col, comp_btn_col = st.columns([5, 1.3])
        with comp_col:
            st.selectbox("🏢 Company", company_options, index=default_idx, key="company_select_widget")
            if st.session_state["company_select_widget"] == NEW_COMPANY_LABEL:
                st.text_input(
                    "New company name",
                    key="company_new_name_draft",
                    value=st.session_state.get(
                        "company_new_name_draft",
                        confirmed_client if confirmed_client not in clients_db else "",
                    ),
                )
        with comp_btn_col:
            st.markdown("<div style='padding-top:28px'>", unsafe_allow_html=True)
            if st.button("✔ Save", key="btn_set_company", use_container_width=True):
                chosen = st.session_state["company_select_widget"]
                new_client = (
                    st.session_state.get("company_new_name_draft", "").strip()
                    if chosen == NEW_COMPANY_LABEL else chosen
                )
                if not new_client:
                    st.session_state["_company_field_error"] = "Enter a company name."
                else:
                    if new_client != confirmed_client:
                        # La empresa cambió → los campos de contacto dependientes
                        # quedan obsoletos y se limpian, junto con sus borradores.
                        st.session_state["quote_contact"]        = ""
                        st.session_state["quote_contact_title"]  = ""
                        st.session_state["quote_contact_mobile"] = ""
                        st.session_state["quote_email"]          = ""
                        for k in ("contact_select_widget", "contact_new_name_draft",
                                  "contact_title_draft", "contact_mobile_draft", "contact_email_draft"):
                            st.session_state.pop(k, None)
                    st.session_state["quote_client"] = new_client
                    st.session_state.pop("_company_field_error", None)
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)

        if st.session_state.get("_company_field_error"):
            st.error(st.session_state["_company_field_error"])
        st.caption(f"✅ Saved company: **{st.session_state.get('quote_client') or '— not set —'}**")

        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

        # ── Contact Name / Contact Title ─────────────────────────────────────
        confirmed_client = st.session_state.get("quote_client", "")
        contacts_list    = clients_db.get(confirmed_client, [])
        contact_names    = [c.get("contact", "") for c in contacts_list if c.get("contact")]
        contact_options  = contact_names + [NEW_CONTACT_LABEL]
        confirmed_contact = st.session_state.get("quote_contact", "")
        default_c_idx = contact_options.index(confirmed_contact) if confirmed_contact in contact_names else len(contact_options) - 1

        cc1, cc2 = st.columns(2)
        with cc1:
            name_col, name_btn_col = st.columns([4, 1.5])
            with name_col:
                if not confirmed_client:
                    st.selectbox("👤 Contact Name", [NEW_CONTACT_LABEL], index=0,
                                 key="contact_select_widget", disabled=True)
                    st.caption("⚠️ Save the company first.")
                else:
                    st.selectbox("👤 Contact Name", contact_options, index=default_c_idx, key="contact_select_widget")
                    if st.session_state["contact_select_widget"] == NEW_CONTACT_LABEL:
                        st.text_input(
                            "New contact name",
                            key="contact_new_name_draft",
                            value=st.session_state.get(
                                "contact_new_name_draft",
                                confirmed_contact if confirmed_contact not in contact_names else "",
                            ),
                        )
            with name_btn_col:
                st.markdown("<div style='padding-top:28px'>", unsafe_allow_html=True)
                if st.button("✔ Save", key="btn_set_contact", use_container_width=True, disabled=not confirmed_client):
                    chosen = st.session_state.get("contact_select_widget", NEW_CONTACT_LABEL)
                    match  = None
                    if chosen == NEW_CONTACT_LABEL:
                        new_contact = st.session_state.get("contact_new_name_draft", "").strip()
                    else:
                        new_contact = chosen
                        match = next((c for c in contacts_list if c.get("contact") == chosen), None)

                    if not new_contact:
                        st.session_state["_contact_field_error"] = "Enter a contact name."
                    else:
                        st.session_state["quote_contact"] = new_contact
                        st.session_state.pop("_contact_field_error", None)
                        if match:
                            # Contacto existente: se heredan sus datos directamente.
                            st.session_state["quote_email"]          = match.get("email", "")
                            st.session_state["quote_contact_title"]  = match.get("title", "")
                            st.session_state["quote_contact_mobile"] = match.get("mobile", "")
                            for k in ("contact_title_draft", "contact_mobile_draft", "contact_email_draft"):
                                st.session_state.pop(k, None)
                    st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)

            if st.session_state.get("_contact_field_error"):
                st.error(st.session_state["_contact_field_error"])
            st.caption(f"✅ Saved contact: **{st.session_state.get('quote_contact') or '— not set —'}**")

        with cc2:
            title_col, title_btn_col = st.columns([4, 1.5])
            with title_col:
                st.text_input(
                    "💼 Contact Title",
                    key="contact_title_draft",
                    value=st.session_state.get("contact_title_draft", st.session_state.get("quote_contact_title", "")),
                )
            with title_btn_col:
                st.markdown("<div style='padding-top:28px'>", unsafe_allow_html=True)
                if st.button("✔ Save", key="btn_set_title", use_container_width=True):
                    st.session_state["quote_contact_title"] = st.session_state.get("contact_title_draft", "").strip()
                    st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)
            st.caption(f"✅ Saved title: **{st.session_state.get('quote_contact_title') or '— not set —'}**")

        # ── Mobile Phone / Email ─────────────────────────────────────────────
        cc3, cc4 = st.columns(2)
        with cc3:
            mob_col, mob_btn_col = st.columns([4, 1.5])
            with mob_col:
                st.text_input(
                    "📱 Mobile Phone",
                    key="contact_mobile_draft",
                    value=st.session_state.get("contact_mobile_draft", st.session_state.get("quote_contact_mobile", "")),
                )
            with mob_btn_col:
                st.markdown("<div style='padding-top:28px'>", unsafe_allow_html=True)
                if st.button("✔ Save", key="btn_set_mobile", use_container_width=True):
                    st.session_state["quote_contact_mobile"] = st.session_state.get("contact_mobile_draft", "").strip()
                    st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)
            st.caption(f"✅ Saved mobile: **{st.session_state.get('quote_contact_mobile') or '— not set —'}**")

        with cc4:
            email_col, email_btn_col = st.columns([4, 1.5])
            with email_col:
                st.text_input(
                    "✉️ Email",
                    key="contact_email_draft",
                    value=st.session_state.get("contact_email_draft", st.session_state.get("quote_email", "")),
                )
            with email_btn_col:
                st.markdown("<div style='padding-top:28px'>", unsafe_allow_html=True)
                if st.button("✔ Save", key="btn_set_email", use_container_width=True):
                    val = st.session_state.get("contact_email_draft", "").strip()
                    if val and not _EMAIL_RE.match(val):
                        st.session_state["_email_field_error"] = "That doesn't look like a valid email."
                    else:
                        st.session_state["quote_email"] = val
                        st.session_state.pop("_email_field_error", None)
                    st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)
            if st.session_state.get("_email_field_error"):
                st.error(st.session_state["_email_field_error"])
            st.caption(f"✅ Saved email: **{st.session_state.get('quote_email') or '— not set —'}**")


def show_client_step():
    """Punto de entrada público: renderiza el Paso 1 completo (formulario +
    Proposal Title/Date + botón Continue con validación)."""
    from tools import quotes_repo

    st.markdown("### 📝 Step 1 — Client &amp; Contact")
    st.caption("Confirm the client and contact details before creating the quote.")

    if "clients_db" not in st.session_state:
        try:
            st.session_state["clients_db"] = quotes_repo.load_clients_db()
        except Exception as e:
            st.session_state["clients_db"] = {}
            st.warning(f"Could not load clients database: {e}")

    clients_db = st.session_state["clients_db"]

    _render_client_contact_form(clients_db)

    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    pc1, pc2 = st.columns(2)
    with pc1:
        st.text_input("📌 Proposal Title", key="quote_title")
    with pc2:
        st.date_input("📅 Date", key="quote_date_obj", format="DD/MM/YYYY")

    st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

    _, col_btn = st.columns([4, 1.3])
    with col_btn:
        continue_clicked = st.button("Continue to Quote →", type="primary", use_container_width=True)

    if continue_clicked:
        errors = validate_client_step()
        if errors:
            for e in errors:
                st.error(f"⚠️ {e}")
        else:
            snapshot_client_info()
            st.session_state["client_step_done"] = True
            st.rerun()
