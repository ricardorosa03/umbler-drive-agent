"""
main.py
API intermediária: Umbler Talk -> Claude Vision -> Google Drive (Núcleo Imobiliário)

Roteamento (apenas documentos enviados pelo CLIENTE, Source=Contact):
  1. Telefone na planilha?  -> salva em {pasta}/Documentos WhatsApp/
  2. Tem tag de outro núcleo (Bancário/PFI)? -> revisão "Outro núcleo"
  3. Tem tag "Cliente"? -> revisão "Cliente sem vínculo" (+ log de candidata)
  4. Lead/novo -> cria {Inicial}/{Nome} {número}, salva, registra na planilha
"""

import os
import json
import logging
import unicodedata
from datetime import datetime

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse

load_dotenv()

from app.document_identifier import identify_document
from app.drive_uploader import (
    create_lead_folder,
    get_review_folder,
    get_whatsapp_subfolder,
    match_client_folder,
    initial_bucket,
    list_bucket_folders,
    match_in_folders,
    upload_file,
)
from app.sheets_mapping import (
    lookup_phone,
    register_row,
    read_all_rows,
    batch_set_suggestions,
)

# ─── Config ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="PFA | Umbler -> Drive Agent", version="2.0.0")

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

# Tag que marca quem já é cliente (ID é mais estável que o nome)
CLIENTE_TAG_ID = "aBlPTBc94SJ_ybrX"
CLIENTE_TAG_NOME = "cliente"
# Tags de OUTROS núcleos (não-imobiliário) — comparadas normalizadas (sem acento)
OUTRO_NUCLEO_TAGS = {"bancario", "pfi"}

SUPPORTED_MIMES = {
    "application/pdf",
    "image/jpeg",
    "image/jpg",
    "image/png",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode()
    return t.lower().strip()


def _so_digitos(t: str) -> str:
    return "".join(c for c in (t or "") if c.isdigit())


# ─── Health ───────────────────────────────────────────────────────────────────
@app.get("/")
def health():
    return {"status": "ok", "service": "PFA Umbler Drive Agent", "version": "2.0.0"}


@app.get("/debug/drives")
def debug_drives():
    """Lista os Shared Drives que a Service Account enxerga.
    Ajuda a confirmar acesso e descobrir o ID correto."""
    try:
        from app.drive_uploader import _get_service, ROOT_DRIVE_ID
        svc = _get_service()
        result = svc.drives().list(pageSize=100, fields="drives(id, name)").execute()
        drives = result.get("drives", [])
        return {
            "root_drive_id_configurado": ROOT_DRIVE_ID,
            "shared_drives_visiveis": drives,
            "total": len(drives),
        }
    except Exception as e:
        return {"erro": str(e)[:300]}


@app.get("/admin/auto-mapear")
def auto_mapear(request: Request):
    """Varre a planilha e, para cada linha sem pasta_drive_id, procura a pasta
    do cliente no Drive e grava a SUGESTÃO (nome em F, ID em G).
    Você revisa e copia o ID sugerido (G) para pasta_drive_id (C) quando confirmar."""
    secret = request.query_params.get("secret", "")
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    rows = read_all_rows()
    cache = {}
    updates = []
    stats = {"unico": 0, "ambiguo": 0, "nenhum": 0, "pulados": 0}

    for r in rows:
        if r["pasta_id"]:          # já tem pasta confirmada -> não mexe
            stats["pulados"] += 1
            continue
        if not r["nome"]:          # sem nome -> não tem como casar
            stats["pulados"] += 1
            continue

        bucket = initial_bucket(r["nome"])
        if bucket not in cache:
            cache[bucket] = list_bucket_folders(bucket)
        m = match_in_folders(r["nome"], cache[bucket])

        if m["status"] == "unico":
            updates.append((r["row"], m["nome"], m["id"]))
            stats["unico"] += 1
        elif m["status"] == "ambiguo":
            updates.append((r["row"], f"AMBÍGUO ({m['qtd']})", ""))
            stats["ambiguo"] += 1
        else:
            updates.append((r["row"], "sem match", ""))
            stats["nenhum"] += 1

    batch_set_suggestions(updates)
    log.info(f"[auto-mapear] {stats} | linhas atualizadas: {len(updates)}")
    return {"ok": True, "stats": stats, "linhas_atualizadas": len(updates)}


# ─── Webhook ──────────────────────────────────────────────────────────────────
@app.post("/webhook/umbler")
async def webhook_umbler(request: Request, background_tasks: BackgroundTasks):
    secret = request.query_params.get("secret", "")
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await request.json()
    if body.get("Type", "") != "MessageFileUploaded":
        return JSONResponse({"ignored": True, "type": body.get("Type")})

    content = body.get("Payload", {}).get("Content", {})
    message = content.get("Message", {})
    file_info = message.get("File")
    if not file_info:
        return JSONResponse({"ignored": True, "reason": "no file"})

    # Só processa o que o CLIENTE enviou
    if message.get("Source", "") != "Contact":
        return JSONResponse({"ignored": True, "reason": "not from client"})

    contact = content.get("Contact", {})
    client_name = contact.get("Name", "Cliente Desconhecido")
    phone = contact.get("PhoneNumber", "")
    tags = contact.get("Tags", []) or []
    chat_id = content.get("Id", "")
    event_id = body.get("EventId", "")

    file_url = file_info.get("Url", "")
    content_type = file_info.get("ContentType", "application/octet-stream")
    original_name = file_info.get("OriginalName", "documento")

    # LOG das tags — para confirmar que chegam no payload ao vivo
    tag_repr = [{"Name": t.get("Name"), "Id": t.get("Id")} for t in tags]
    log.info(f"[{event_id}] Arquivo de '{client_name}' ({phone}) | tags={tag_repr} | {original_name}")

    if content_type not in SUPPORTED_MIMES:
        return JSONResponse({"ignored": True, "reason": f"unsupported mime: {content_type}"})

    background_tasks.add_task(
        process_file,
        event_id=event_id,
        file_url=file_url,
        content_type=content_type,
        original_name=original_name,
        client_name=client_name,
        phone=phone,
        tags=tags,
    )
    return JSONResponse({"received": True, "event_id": event_id})


# ─── Decisão de destino ───────────────────────────────────────────────────────
def resolve_destination(client_name: str, phone: str, tags: list, event_id: str) -> str:
    """Aplica a árvore de roteamento e retorna o folder_id de DESTINO do upload."""
    phone_digits = _so_digitos(phone)

    # 1. Consulta a planilha
    info = lookup_phone(phone_digits)

    if info["status"] == "confirmado":
        log.info(f"[{event_id}] Telefone confirmado na planilha -> {info['pasta_id']}")
        return get_whatsapp_subfolder(info["pasta_id"])

    ja_pendente = info["status"] == "pendente"

    tag_names = {_norm(t.get("Name", "")) for t in tags}
    tag_ids = {t.get("Id", "") for t in tags}

    # 2. Outro núcleo (Bancário/PFI) -> revisão
    if tag_names & OUTRO_NUCLEO_TAGS:
        log.info(f"[{event_id}] Tag de outro núcleo -> revisão")
        if not ja_pendente:
            register_row(phone_digits, client_name, "", "revisao-outro-nucleo", "")
        return get_review_folder("Outro núcleo", client_name, phone_digits)

    # 3. Cliente imobiliário sem vínculo -> revisão (+ candidata + ID na planilha)
    if CLIENTE_TAG_ID in tag_ids or CLIENTE_TAG_NOME in tag_names:
        m = match_client_folder(client_name)
        if m["status"] == "unico":
            cand, id_sug = m["nome"], m["id"]
        elif m["status"] == "ambiguo":
            cand, id_sug = f"AMBÍGUO ({m['qtd']})", ""
        else:
            cand, id_sug = "", ""
        log.info(f"[{event_id}] Cliente sem vínculo. Candidata: {cand or 'nenhuma'}")
        if not ja_pendente:
            register_row(phone_digits, client_name, "", "revisao-cliente", cand, id_sug)
        return get_review_folder("Cliente sem vínculo", client_name, phone_digits)

    # 4. Lead/novo -> cria pasta, salva e registra vínculo CONFIRMADO
    log.info(f"[{event_id}] Lead/novo -> criando pasta")
    main_folder = create_lead_folder(client_name, phone_digits)
    if not ja_pendente:
        register_row(phone_digits, client_name, main_folder, "auto-lead", "")
    return get_whatsapp_subfolder(main_folder)


# ─── Processamento ────────────────────────────────────────────────────────────
async def process_file(event_id, file_url, content_type, original_name, client_name, phone, tags):
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(file_url)
            if resp.status_code == 401:
                resp = await client.get(
                    file_url, headers={"Authorization": os.environ.get("UMBLER_API_TOKEN", "")}
                )
            resp.raise_for_status()
            file_bytes = resp.content
        log.info(f"[{event_id}] Arquivo baixado: {len(file_bytes)} bytes")

        identification = identify_document(file_bytes, content_type, original_name, client_name)
        nome_final = identification["nome_final"]
        log.info(f"[{event_id}] Identificado: {identification['tipo']} -> {nome_final}")

        destino_id = resolve_destination(client_name, phone, tags, event_id)

        drive_link = upload_file(file_bytes, nome_final, content_type, destino_id)
        log.info(f"[{event_id}] ✅ Salvo no Drive: {drive_link}")

        log.info(json.dumps({
            "event": "file_processed",
            "event_id": event_id,
            "client": client_name,
            "phone": _so_digitos(phone),
            "tipo": identification["tipo"],
            "nome_final": nome_final,
            "drive_link": drive_link,
            "timestamp": datetime.utcnow().isoformat(),
        }))
    except Exception as e:
        log.error(f"[{event_id}] ❌ Erro ao processar arquivo: {e}", exc_info=True)
