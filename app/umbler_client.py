"""
umbler_client.py
Envia mensagem de confirmação ao cliente via API Umbler Talk.
"""

import os
import httpx

BASE_URL = "https://app-utalk.umbler.com/api"
TOKEN = os.environ.get("UMBLER_API_TOKEN", "")  # inclui "Bearer "
ORG_ID = os.environ.get("UMBLER_ORG_ID", "")


async def send_confirmation(chat_id: str, file_name: str, drive_link: str) -> bool:
    """
    Envia mensagem de confirmação ao cliente no chat indicado.
    Retorna True se enviou com sucesso.
    """
    message = (
        f"✅ Documento recebido e arquivado com sucesso!\n"
        f"📄 *{file_name}*\n"
        f"Qualquer dúvida, estamos à disposição."
    )

    headers = {
        "Authorization": TOKEN,
        "Content-Type": "application/json",
    }
    payload = {
        "organizationId": ORG_ID,
        "message": message,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{BASE_URL}/v1/chats/{chat_id}/messages",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            return True
    except Exception as e:
        print(f"[Umbler] Falha ao enviar confirmação: {e}")
        return False
