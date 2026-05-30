"""
main.py
API intermediária: Umbler Talk → Claude Vision → Google Drive
"""

import os
import json
import logging
from datetime import datetime

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse

load_dotenv()

from app.document_identifier import identify_document
from app.drive_uploader import get_client_folder_id, upload_file
from app.umbler_client import send_confirmation

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ─── App ─────────────────────────────────────────────────────────────────────
app = FastAPI(title="PFA | Umbler → Drive Agent", version="1.0.0")

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
SUPPORTED_MIMES = {
    "application/pdf",
    "image/jpeg",
    "image/jpg",
    "image/png",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


# ─── Health check ─────────────────────────────────────────────────────────────
@app.get("/")
def health():
    return {"status": "ok", "service": "PFA Umbler Drive Agent"}


# ─── Webhook principal ────────────────────────────────────────────────────────
@app.post("/webhook/umbler")
async def webhook_umbler(request: Request, background_tasks: BackgroundTasks):
    # 1. Valida secret na query string
    secret = request.query_params.get("secret", "")
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        log.warning("Webhook recusado: secret inválido")
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await request.json()
    event_type = body.get("Type", "")

    # 2. Filtra apenas MessageFileUploaded
    if event_type != "MessageFileUploaded":
        return JSONResponse({"ignored": True, "type": event_type})

    payload = body.get("Payload", {})
    content = payload.get("Content", {})
    message = content.get("Message", {})
    file_info = message.get("File")

    if not file_info:
        return JSONResponse({"ignored": True, "reason": "no file"})

    # 3. Extrai campos relevantes
    file_url = file_info.get("Url", "")
    content_type = file_info.get("ContentType", "application/octet-stream")
    original_name = file_info.get("OriginalName", "documento")
    client_name = content.get("Contact", {}).get("Name", "Cliente Desconhecido")
    phone = content.get("Contact", {}).get("PhoneNumber", "")
    chat_id = content.get("Id", "")
    event_id = body.get("EventId", "")

    log.info(f"[{event_id}] Arquivo recebido de '{client_name}' ({phone}): {original_name}")

    # 4. Verifica tipo suportado
    if content_type not in SUPPORTED_MIMES:
        log.info(f"[{event_id}] Tipo não suportado: {content_type} — ignorando")
        return JSONResponse({"ignored": True, "reason": f"unsupported mime: {content_type}"})

    # 5. Processa em background (retorna 200 imediatamente pro Umbler)
    background_tasks.add_task(
        process_file,
        event_id=event_id,
        file_url=file_url,
        content_type=content_type,
        original_name=original_name,
        client_name=client_name,
        chat_id=chat_id,
    )

    return JSONResponse({"received": True, "event_id": event_id})


# ─── Processamento em background ──────────────────────────────────────────────
async def process_file(
    event_id: str,
    file_url: str,
    content_type: str,
    original_name: str,
    client_name: str,
    chat_id: str,
):
    try:
        # 1. Baixa o arquivo
        log.info(f"[{event_id}] Baixando arquivo: {file_url}")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(file_url)
            if resp.status_code == 401:
                # Tenta com Bearer token da Umbler
                umbler_token = os.environ.get("UMBLER_API_TOKEN", "")
                resp = await client.get(
                    file_url, headers={"Authorization": umbler_token}
                )
            resp.raise_for_status()
            file_bytes = resp.content

        log.info(f"[{event_id}] Arquivo baixado: {len(file_bytes)} bytes")

        # 2. Identifica o documento via Claude Vision
        identification = identify_document(
            file_bytes=file_bytes,
            content_type=content_type,
            original_name=original_name,
            client_name=client_name,
        )
        nome_final = identification["nome_final"]
        tipo = identification["tipo"]
        confianca = identification["confianca"]

        log.info(f"[{event_id}] Identificado como: {tipo} (confiança: {confianca}) → {nome_final}")

        # 3. Obtém/cria pasta do cliente no Drive
        folder_id = get_client_folder_id(client_name)

        # 4. Faz upload
        drive_link = upload_file(
            file_bytes=file_bytes,
            file_name=nome_final,
            content_type=content_type,
            folder_id=folder_id,
        )

        log.info(f"[{event_id}] ✅ Salvo no Drive: {drive_link}")

        # 5. Confirma para o cliente via WhatsApp
        if chat_id:
            await send_confirmation(chat_id, nome_final, drive_link)

        # 6. Log estruturado final
        log.info(
            json.dumps({
                "event": "file_processed",
                "event_id": event_id,
                "client": client_name,
                "tipo": tipo,
                "confianca": confianca,
                "nome_final": nome_final,
                "drive_link": drive_link,
                "timestamp": datetime.utcnow().isoformat(),
            })
        )

    except Exception as e:
        log.error(f"[{event_id}] ❌ Erro ao processar arquivo: {e}", exc_info=True)
