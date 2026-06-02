"""
sheets_mapping.py
Gerencia o vínculo telefone -> pasta do Drive numa planilha Google Sheets.
A planilha funciona como o "mapa" que o agent consulta e alimenta.

Colunas (linha 1 = cabeçalho):
  A: telefone | B: nome | C: pasta_drive_id | D: criado_em | E: origem
"""

import os
import datetime

from googleapiclient.discovery import build
from app.drive_uploader import _get_credentials  # reaproveita as credenciais

SHEET_ID = os.environ.get("GOOGLE_SHEETS_MAP_ID", "").strip().strip('"').strip("'")
# Fallback: se a env var não chegar (problema de parsing no painel), usa o ID fixo
if not SHEET_ID:
    SHEET_ID = "1wfgQIfOda6XmntL9ppkTKPMaGi_4t327WZJ6e0eCpW8"
RANGE_LEITURA = "A2:E"  # ignora o cabeçalho
_sheets = None


def _get_sheets_service():
    global _sheets
    if _sheets is None:
        creds = _get_credentials()
        _sheets = build("sheets", "v4", credentials=creds)
    return _sheets


def _so_digitos(telefone: str) -> str:
    """Normaliza telefone para apenas dígitos (ex: +55 48 9... -> 55489...)."""
    return "".join(c for c in (telefone or "") if c.isdigit())


def lookup_folder_by_phone(telefone: str) -> str | None:
    """Procura o telefone na planilha. Retorna o pasta_drive_id ou None."""
    alvo = _so_digitos(telefone)
    if not alvo:
        return None

    svc = _get_sheets_service()
    result = (
        svc.spreadsheets()
        .values()
        .get(spreadsheetId=SHEET_ID, range=RANGE_LEITURA)
        .execute()
    )
    linhas = result.get("values", [])
    for linha in linhas:
        if not linha:
            continue
        tel_planilha = _so_digitos(linha[0])
        if tel_planilha and tel_planilha == alvo:
            # coluna C (índice 2) = pasta_drive_id
            if len(linha) >= 3 and linha[2].strip():
                return linha[2].strip()
    return None


def register_mapping(telefone: str, nome: str, pasta_id: str, origem: str) -> bool:
    """Adiciona uma nova linha de vínculo na planilha."""
    try:
        svc = _get_sheets_service()
        agora = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        nova_linha = [[_so_digitos(telefone), nome, pasta_id, agora, origem]]
        svc.spreadsheets().values().append(
            spreadsheetId=SHEET_ID,
            range="A:E",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": nova_linha},
        ).execute()
        return True
    except Exception as e:
        print(f"[Sheets] Falha ao registrar vínculo: {e}")
        return False
