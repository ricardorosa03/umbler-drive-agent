"""
document_identifier.py
Usa Claude Vision para identificar o tipo do documento e sugerir nome padronizado.
"""

import base64
import re
from datetime import datetime
from pathlib import Path

import anthropic
import fitz  # PyMuPDF


ANTHROPIC_CLIENT = anthropic.Anthropic()

PROMPT = """Você é um assistente jurídico especializado em identificação de documentos.
Analise a imagem/documento enviado e responda APENAS com um JSON no formato abaixo, sem explicações:

{
  "tipo": "TIPO_DO_DOCUMENTO",
  "confianca": "alta|media|baixa"
}

Tipos aceitos (use EXATAMENTE um destes):
RG, CPF, CNH, PASSAPORTE, CERTIDAO_NASCIMENTO, CERTIDAO_CASAMENTO,
COMPROVANTE_RESIDENCIA, COMPROVANTE_RENDA, CONTRATO, PROCURACAO,
LAUDO_MEDICO, EXAME, BOLETIM_OCORRENCIA, ESCRITURA, MATRICULA_IMOVEL,
NOTA_FISCAL, RECIBO, BOLETO, EXTRATO_BANCARIO, FOTO, DOCUMENTO

Se não conseguir identificar com segurança, use DOCUMENTO."""


def _pdf_to_image_base64(pdf_bytes: bytes) -> str:
    """Converte a primeira página do PDF em PNG base64."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    mat = fitz.Matrix(2.0, 2.0)  # 2x para melhor resolução
    pix = page.get_pixmap(matrix=mat)
    png_bytes = pix.tobytes("png")
    doc.close()
    return base64.standard_b64encode(png_bytes).decode("utf-8")


def _image_to_base64(image_bytes: bytes) -> str:
    return base64.standard_b64encode(image_bytes).decode("utf-8")


def identify_document(
    file_bytes: bytes,
    content_type: str,
    original_name: str,
    client_name: str,
) -> dict:
    """
    Envia o documento para o Claude e retorna:
    {
        "tipo": "CONTRATO",
        "confianca": "alta",
        "nome_final": "CONTRATO_JOAO_PEDRO_2026-05-29.pdf"
    }
    """
    ext = Path(original_name).suffix.lower() or _ext_from_mime(content_type)
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    cliente_slug = re.sub(r"[^A-Z0-9]", "_", client_name.upper().strip())

    # Prepara conteúdo para o Claude
    if content_type == "application/pdf":
        image_b64 = _pdf_to_image_base64(file_bytes)
        media_type = "image/png"
    elif content_type in ("image/jpeg", "image/jpg"):
        image_b64 = _image_to_base64(file_bytes)
        media_type = "image/jpeg"
    elif content_type == "image/png":
        image_b64 = _image_to_base64(file_bytes)
        media_type = "image/png"
    else:
        # Tipo não suportado para visão: usa nome original
        return {
            "tipo": "DOCUMENTO",
            "confianca": "baixa",
            "nome_final": f"DOCUMENTO_{cliente_slug}_{date_str}{ext}",
        }

    try:
        response = ANTHROPIC_CLIENT.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": PROMPT},
                    ],
                }
            ],
        )

        raw = response.content[0].text.strip()
        # Extrai JSON mesmo que venha com ```json ... ```
        json_match = re.search(r"\{.*?\}", raw, re.DOTALL)
        if not json_match:
            raise ValueError("JSON não encontrado na resposta")

        import json
        result = json.loads(json_match.group())
        tipo = result.get("tipo", "DOCUMENTO").upper()
        confianca = result.get("confianca", "baixa")

    except Exception as e:
        print(f"[Claude Vision] Erro: {e} — usando fallback")
        tipo = "DOCUMENTO"
        confianca = "baixa"

    nome_final = f"{tipo}_{cliente_slug}_{date_str}{ext}"
    return {"tipo": tipo, "confianca": confianca, "nome_final": nome_final}


def _ext_from_mime(mime: str) -> str:
    mapping = {
        "application/pdf": ".pdf",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "application/msword": ".doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    }
    return mapping.get(mime, ".bin")
