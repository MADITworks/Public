"""
Lectura de clientes/contactos ya existentes, para uso EXCLUSIVO del Paso 1
de Quotes (client_contact_step.py).

Este módulo es deliberadamente el ÚNICO punto de contacto entre Quotes y el
módulo Clientes, y es de un solo sentido: SOLO LEE. Nunca crea, actualiza ni
borra nada en clients.json — eso es responsabilidad exclusiva del módulo
Clientes. Un cliente/contacto que no exista ahí simplemente no aparece aquí
como opción; no hay "crear nuevo" en este flujo.

Si el módulo Clientes cambia su lógica interna (validaciones, UI, etc.) esto
no se entera y sigue funcionando igual, siempre que:
  - el archivo siga en BASE_PATH/Clients/clients.json dentro del mismo repo
    privado de GitHub, y
  - la forma del JSON siga siendo:
      {"Nombre Cliente": [{"contact": ..., "title": ..., "mobile": ...,
                            "email": ...}, ...], ...}

Si esa ruta o esa forma cambian, hay que actualizar SOLO este archivo — el
resto de Quotes no debería tocarse.
"""

import base64
import json
import requests
import streamlit as st


BASE_PATH = "Propietary_tools"


def _headers():
    return {
        "Authorization": f"Bearer {st.secrets['github']['token']}",
        "Accept": "application/vnd.github+json",
    }


def _repo_base():
    owner = st.secrets["github"]["owner"]
    repo  = st.secrets["github"]["private_repo"]
    return f"https://api.github.com/repos/{owner}/{repo}"


def load_clients_readonly() -> dict:
    """
    Devuelve {client_name: [{"contact":, "title":, "mobile":, "email":}, ...]}
    tal como lo dejó el módulo Clientes. Vacío si el archivo aún no existe
    (p.ej. todavía no se ha creado ningún cliente).
    """
    url = f"{_repo_base()}/contents/{BASE_PATH}/Clients/clients.json"
    r   = requests.get(url, headers=_headers())
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    content = base64.b64decode(r.json()["content"]).decode()
    return json.loads(content)
