"""
drive_uploader.py
Operações no Google Drive (Shared Drive "Núcleo Imobiliário") via Service Account.

Estrutura alvo:
  Núcleo Imobiliário (Shared Drive)
    └── {Inicial}            ex: "Jj"
          └── {Nome} {número}    ex: "João Silva 5548988616862"
                └── Documentos WhatsApp
                      └── arquivos

Pasta de revisão (quando não dá pra rotear com segurança):
  Núcleo Imobiliário
    └── _A Revisar WhatsApp
          ├── Cliente sem vínculo / {Nome} {número}
          └── Outro núcleo       / {Nome} {número}
"""

import base64
import io
import json
import os
import unicodedata

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# Escopos: Drive (arquivos/pastas) + Spreadsheets (planilha de mapa)
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

# ID do SHARED DRIVE "Núcleo Imobiliário" (começa com 0A...)
ROOT_DRIVE_ID = os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID", "").strip().strip('"').strip("'").lstrip("\\").strip('"')
if not ROOT_DRIVE_ID:
    ROOT_DRIVE_ID = "0ACQVPouU4waiUk9PVA"

# Trava de segurança: IDs de Shared Drive começam com "0A". Se a variável vier
# com um ID de pasta comum antigo (ex: a pasta de teste "1r2A..."), ignora e usa
# o Shared Drive correto. Evita o erro "Shared drive not found".
if not ROOT_DRIVE_ID.startswith("0A"):
    print(f"[drive] AVISO: ROOT_DRIVE_ID '{ROOT_DRIVE_ID}' não é Shared Drive; usando 0ACQVPouU4waiUk9PVA")
    ROOT_DRIVE_ID = "0ACQVPouU4waiUk9PVA"

print(f"[drive] ROOT_DRIVE_ID em uso: {ROOT_DRIVE_ID}")

WHATSAPP_SUBFOLDER = "Documentos WhatsApp"
REVIEW_FOLDER = "_A Revisar WhatsApp"

_creds = None
_service = None


# ─── Credenciais (compartilhadas com sheets_mapping) ──────────────────────────
def _load_service_account_info() -> dict:
    """Carrega o JSON da Service Account. Aceita JSON puro OU Base64."""
    raw = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"].strip()
    if (raw.startswith("'") and raw.endswith("'")) or (
        raw.startswith('"') and raw.endswith('"')
    ):
        raw = raw[1:-1]

    if not raw.lstrip().startswith("{"):
        cleaned = "".join(raw.split())
        missing_padding = len(cleaned) % 4
        if missing_padding:
            cleaned += "=" * (4 - missing_padding)
        decoded = base64.b64decode(cleaned).decode("utf-8")
        info = json.loads(decoded)
        if "private_key" in info and "\\n" in info["private_key"]:
            info["private_key"] = info["private_key"].replace("\\n", "\n")
        return info

    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        info = json.loads(raw.replace("\\n", "\n").replace('\\"', '"'))
    if "private_key" in info and "\\n" in info["private_key"]:
        info["private_key"] = info["private_key"].replace("\\n", "\n")
    return info


def _get_credentials():
    global _creds
    if _creds is None:
        _creds = service_account.Credentials.from_service_account_info(
            _load_service_account_info(), scopes=SCOPES
        )
    return _creds


def _get_service():
    global _service
    if _service is None:
        _service = build("drive", "v3", credentials=_get_credentials())
    return _service


# ─── Helpers de pasta (cientes de Shared Drive) ───────────────────────────────
def _find_folder(name: str, parent_id: str) -> str | None:
    """Procura uma subpasta pelo nome dentro de parent_id. Retorna id ou None."""
    svc = _get_service()
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
            corpora="drive",
            driveId=ROOT_DRIVE_ID,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    files = results.get("files", [])
    return files[0]["id"] if files else None


def _create_folder(name: str, parent_id: str) -> str:
    svc = _get_service()
    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = (
        svc.files().create(body=metadata, fields="id", supportsAllDrives=True).execute()
    )
    return folder["id"]


def _find_or_create_folder(name: str, parent_id: str) -> str:
    existing = _find_folder(name, parent_id)
    return existing if existing else _create_folder(name, parent_id)


# ─── Regra de inicial (Aa, Bb, Cc...) ─────────────────────────────────────────
def initial_bucket(nome: str) -> str:
    """Primeira letra do nome, sem acento, no formato 'Xx'. Fallback '_Outros'."""
    nome = (nome or "").strip()
    if not nome:
        return "_Outros"
    # remove acento
    primeira = unicodedata.normalize("NFKD", nome[0]).encode("ascii", "ignore").decode()
    primeira = primeira.upper()
    if "A" <= primeira <= "Z":
        return f"{primeira}{primeira.lower()}"
    return "_Outros"


def _normalize(texto: str) -> str:
    """lowercase + sem acento + trim, para comparação de nomes."""
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return t.lower().strip()


# ─── Destinos ─────────────────────────────────────────────────────────────────
def get_whatsapp_subfolder(main_folder_id: str) -> str:
    """Dado o id da pasta principal do cliente, garante a subpasta
    'Documentos WhatsApp' dentro dela e retorna o id."""
    return _find_or_create_folder(WHATSAPP_SUBFOLDER, main_folder_id)


def create_lead_folder(nome: str, telefone_digits: str) -> str:
    """Cria (ou acha) {Inicial}/{Nome} {número} e retorna o id da pasta PRINCIPAL."""
    bucket_id = _find_or_create_folder(initial_bucket(nome), ROOT_DRIVE_ID)
    nome_pasta = f"{nome} {telefone_digits}".strip()
    return _find_or_create_folder(nome_pasta, bucket_id)


def get_review_folder(motivo: str, nome: str, telefone_digits: str) -> str:
    """Retorna id de _A Revisar WhatsApp/{motivo}/{Nome} {número}."""
    review_id = _find_or_create_folder(REVIEW_FOLDER, ROOT_DRIVE_ID)
    motivo_id = _find_or_create_folder(motivo, review_id)
    nome_pasta = f"{nome} {telefone_digits}".strip()
    return _find_or_create_folder(nome_pasta, motivo_id)


def find_client_candidate(nome: str) -> str | None:
    """Best-effort: procura no bucket da inicial uma pasta com nome
    EXATAMENTE igual (normalizado). Usado só para LOG, nunca para rotear.
    Retorna o nome da pasta candidata, ou None."""
    try:
        bucket_id = _find_folder(initial_bucket(nome), ROOT_DRIVE_ID)
        if not bucket_id:
            return None
        svc = _get_service()
        results = (
            svc.files()
            .list(
                q=(
                    f"mimeType='application/vnd.google-apps.folder' "
                    f"and '{bucket_id}' in parents and trashed=false"
                ),
                fields="files(id, name)",
                corpora="drive",
                driveId=ROOT_DRIVE_ID,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                pageSize=1000,
            )
            .execute()
        )
        alvo = _normalize(nome)
        matches = [f["name"] for f in results.get("files", []) if _normalize(f["name"]) == alvo]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return f"MÚLTIPLAS ({len(matches)}): {', '.join(matches)}"
        return None
    except Exception as e:
        print(f"[Drive] find_client_candidate falhou: {e}")
        return None


# ─── Upload ───────────────────────────────────────────────────────────────────
def upload_file(file_bytes: bytes, file_name: str, content_type: str, folder_id: str) -> str:
    svc = _get_service()
    metadata = {"name": file_name, "parents": [folder_id]}
    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=content_type, resumable=False)
    file = (
        svc.files()
        .create(body=metadata, media_body=media, fields="id, webViewLink", supportsAllDrives=True)
        .execute()
    )
    return file.get("webViewLink", f"https://drive.google.com/file/d/{file['id']}/view")
