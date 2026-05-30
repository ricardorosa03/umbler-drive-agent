"""
drive_uploader.py
Gerencia pastas por cliente e faz upload de arquivos no Google Drive
via Service Account. Compatível com Meu Drive E Drives Compartilhados.
"""

import base64
import io
import json
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

SCOPES = ["https://www.googleapis.com/auth/drive"]
ROOT_FOLDER_ID = os.environ["GOOGLE_DRIVE_ROOT_FOLDER_ID"]

_service = None


def _get_service():
    global _service
    if _service is None:
        sa_info = _load_service_account_info()
        creds = service_account.Credentials.from_service_account_info(
            sa_info, scopes=SCOPES
        )
        _service = build("drive", "v3", credentials=creds)
    return _service


def _load_service_account_info() -> dict:
    """Carrega o JSON da Service Account de forma robusta.

    Aceita dois formatos na variável GOOGLE_SERVICE_ACCOUNT_JSON:
    1. JSON puro (linha única ou com quebras)
    2. JSON codificado em Base64 (recomendado — evita problemas de
       truncamento, aspas e quebras de linha no Railway)
    """
    raw = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"].strip()

    # Remove aspas externas acidentais
    if (raw.startswith("'") and raw.endswith("'")) or (
        raw.startswith('"') and raw.endswith('"')
    ):
        raw = raw[1:-1]

    # Se NÃO for um JSON cru (não começa com '{'), trata como Base64.
    if not raw.lstrip().startswith("{"):
        # Remove qualquer whitespace que o painel possa ter injetado
        # (espaços, quebras de linha, tabs) — Base64 válido não os tem.
        cleaned = "".join(raw.split())
        # Corrige padding se necessário
        missing_padding = len(cleaned) % 4
        if missing_padding:
            cleaned += "=" * (4 - missing_padding)
        decoded = base64.b64decode(cleaned).decode("utf-8")
        info = json.loads(decoded)
        if "private_key" in info and "\\n" in info["private_key"]:
            info["private_key"] = info["private_key"].replace("\\n", "\n")
        return info

    # Caso seja JSON cru
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        info = json.loads(raw.replace("\\n", "\n").replace('\\"', '"'))

    if "private_key" in info and "\\n" in info["private_key"]:
        info["private_key"] = info["private_key"].replace("\\n", "\n")

    return info


def _find_or_create_folder(name: str, parent_id: str) -> str:
    """Retorna o ID de uma pasta existente ou cria uma nova.
    Funciona tanto em Meu Drive quanto em Drives Compartilhados."""
    svc = _get_service()
    # Escapa aspas simples no nome para evitar quebra na query
    safe_name = name.replace("'", "\\'")
    query = (
        f"name='{safe_name}' and mimeType='application/vnd.google-apps.folder' "
        f"and '{parent_id}' in parents and trashed=false"
    )
    results = (
        svc.files()
        .list(
            q=query,
            fields="files(id, name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    files = results.get("files", [])
    if files:
        return files[0]["id"]

    # Cria pasta nova
    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = (
        svc.files()
        .create(body=metadata, fields="id", supportsAllDrives=True)
        .execute()
    )
    return folder["id"]


def get_client_folder_id(client_name: str) -> str:
    """
    Garante que exista /ROOT/Clientes/{client_name}/ e retorna o ID.
    Estrutura: ROOT_FOLDER -> Clientes -> {Nome do Cliente}
    """
    clientes_id = _find_or_create_folder("Clientes", ROOT_FOLDER_ID)
    client_id = _find_or_create_folder(client_name, clientes_id)
    return client_id


def upload_file(
    file_bytes: bytes,
    file_name: str,
    content_type: str,
    folder_id: str,
) -> str:
    """
    Faz upload do arquivo na pasta indicada.
    Retorna o link de visualização (webViewLink).
    """
    svc = _get_service()
    metadata = {"name": file_name, "parents": [folder_id]}
    media = MediaIoBaseUpload(
        io.BytesIO(file_bytes),
        mimetype=content_type,
        resumable=False,
    )
    file = (
        svc.files()
        .create(
            body=metadata,
            media_body=media,
            fields="id, webViewLink",
            supportsAllDrives=True,
        )
        .execute()
    )
    return file.get("webViewLink", f"https://drive.google.com/file/d/{file['id']}/view")
