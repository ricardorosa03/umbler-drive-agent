"""
sheets_mapping.py
Gerencia o vínculo telefone -> pasta do Drive numa planilha Google Sheets.

Colunas (linha 1 = cabeçalho):
  A: telefone | B: nome | C: pasta_drive_id | D: criado_em | E: origem | F: pasta_candidata

Estados de uma linha:
  - pasta_drive_id PREENCHIDO  -> vínculo confirmado (usa direto, caminho rápido)
  - pasta_drive_id VAZIO       -> pendente de revisão (você ainda vai preencher)
"""

import os
import datetime

from googleapiclient.discovery import build
from app.drive_uploader import _get_credentials  # reaproveita as credenciais

SHEET_ID = os.environ.get("GOOGLE_SHEETS_MAP_ID", "").strip().strip('"').strip("'")
if not SHEET_ID:
    SHEET_ID = "1wfgQIfOda6XmntL9ppkTKPMaGi_4t327WZJ6e0eCpW8"
RANGE_LEITURA = "A2:F"  # ignora o cabeçalho
_sheets = None


def _get_sheets_service():
    global _sheets
    if _sheets is None:
        _sheets = build("sheets", "v4", credentials=_get_credentials())
    return _sheets


def _so_digitos(telefone: str) -> str:
    return "".join(c for c in (telefone or "") if c.isdigit())


def _celula_para_telefone(valor) -> str:
    """Converte uma célula (texto OU número) em telefone só-dígitos.
    Lida com o caso de gravações antigas que viraram número."""
    if isinstance(valor, float):
        # número grande pode vir como float; remove o .0
        valor = format(int(valor), "d")
    elif isinstance(valor, int):
        valor = str(valor)
    return _so_digitos(str(valor))


def lookup_phone(telefone: str) -> dict:
    """Procura o telefone na planilha.
    Retorna:
      {"status": "confirmado", "pasta_id": "..."}  -> tem pasta vinculada
      {"status": "pendente"}                        -> já está na planilha, sem pasta
      {"status": "novo"}                            -> não está na planilha
    """
    alvo = _so_digitos(telefone)
    if not alvo:
        return {"status": "novo"}

    svc = _get_sheets_service()
    result = (
        svc.spreadsheets()
        .values()
        .get(
            spreadsheetId=SHEET_ID,
            range=RANGE_LEITURA,
            valueRenderOption="UNFORMATTED_VALUE",  # evita notação científica
        )
        .execute()
    )
    for linha in result.get("values", []):
        if not linha:
            continue
        if _celula_para_telefone(linha[0]) == alvo:
            pasta_id = str(linha[2]).strip() if len(linha) >= 3 else ""
            if pasta_id:
                return {"status": "confirmado", "pasta_id": pasta_id}
            return {"status": "pendente"}
    return {"status": "novo"}


def register_row(telefone: str, nome: str, pasta_id: str, origem: str, candidata: str = "") -> bool:
    """Adiciona uma linha na planilha.
    Para leads: pasta_id preenchido.
    Para revisão: pasta_id = "" (vazio), candidata com a dica de pasta."""
    try:
        svc = _get_sheets_service()
        agora = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        nova_linha = [[_so_digitos(telefone), nome, pasta_id, agora, origem, candidata]]
        svc.spreadsheets().values().append(
            spreadsheetId=SHEET_ID,
            range="A:F",
            valueInputOption="RAW",  # grava como texto puro — não vira número
            insertDataOption="INSERT_ROWS",
            body={"values": nova_linha},
        ).execute()
        return True
    except Exception as e:
        print(f"[Sheets] Falha ao registrar linha: {e}")
        return False
