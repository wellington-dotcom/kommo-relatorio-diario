"""
estimativa_meta_kommo.py

Cruza dados que JA existem na planilha:
  - aba "Meta Ads Report"     -> gasto e conversas iniciadas, por dia
  - aba "Leads Novos por Dia" -> quantos leads novos entraram no Kommo, por dia

E escreve uma aba nova "Estimativa Meta x Kommo" com a correlacao por dia:
gasto total, conversas iniciadas (Meta), leads novos (Kommo) e um
custo-por-lead aproximado.

IMPORTANTE: isso e uma estimativa agregada por periodo, NAO identifica
qual lead especifico veio de qual anuncio/criativo. Para isso seria
necessario o WhatsApp Business API oficial (ctwa_clid).

Reaproveita os secrets que ja existem: GOOGLE_CREDENTIALS e SHEET_ID.
"""

import os
import json
import gspread
from collections import defaultdict

GOOGLE_CREDENTIALS = json.loads(os.environ["GOOGLE_CREDENTIALS"])
SHEET_ID = os.environ["SHEET_ID"]

ABA_META_ADS = "Meta Ads Report"
ABA_LEADS_NOVOS = "Leads Novos por Dia"
ABA_SAIDA = "Estimativa Meta x Kommo"

# Nomes de coluna esperados em cada aba. Se os nomes reais forem
# diferentes, ajuste aqui (e so isso que precisa mudar).
COL_DATA_META = "date_start"
COL_SPEND = "spend"
COL_CONVERSAS = "actions__onsite_conversion.messaging_conversation_started_7d"

COL_DATA_KOMMO = "Data"
COL_LEADS_NOVOS = "Leads Novos"


def conectar_planilha():
    gc = gspread.service_account_from_dict(GOOGLE_CREDENTIALS)
    return gc.open_by_key(SHEET_ID)


def para_float(valor):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return 0.0


def agregar_meta_por_dia(sh):
    ws = sh.worksheet(ABA_META_ADS)
    registros = ws.get_all_records()

    por_dia = defaultdict(lambda: {"spend": 0.0, "conversas": 0.0})
    for row in registros:
        data = row.get(COL_DATA_META)
        if not data:
            continue
        por_dia[data]["spend"] += para_float(row.get(COL_SPEND))
        por_dia[data]["conversas"] += para_float(row.get(COL_CONVERSAS))

    return por_dia


def ler_leads_por_dia(sh):
    ws = sh.worksheet(ABA_LEADS_NOVOS)
    registros = ws.get_all_records()

    por_dia = {}
    for row in registros:
        data = row.get(COL_DATA_KOMMO)
        if not data:
            continue
        por_dia[data] = para_float(row.get(COL_LEADS_NOVOS))

    return por_dia


def montar_tabela(meta_por_dia, leads_por_dia):
    todas_datas = sorted(set(meta_por_dia.keys()) | set(leads_por_dia.keys()))

    header = [
        "Data",
        "Gasto Total (Meta)",
        "Conversas Iniciadas (Meta)",
        "Leads Novos (Kommo)",
        "Custo por Lead Estimado",
    ]
    linhas = [header]

    for data in todas_datas:
        gasto = meta_por_dia.get(data, {}).get("spend", 0.0)
        conversas = meta_por_dia.get(data, {}).get("conversas", 0.0)
        leads = leads_por_dia.get(data, 0.0)
        custo_por_lead = round(gasto / leads, 2) if leads else ""

        linhas.append([data, round(gasto, 2), round(conversas, 2), leads, custo_por_lead])

    return linhas


def escrever_saida(sh, linhas):
    try:
        ws = sh.worksheet(ABA_SAIDA)
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(
            title=ABA_SAIDA, rows=len(linhas) + 10, cols=len(linhas[0]) + 2
        )

    ws.update(linhas, "A1")
    ws.freeze(rows=1)


def main():
    sh = conectar_planilha()
    meta_por_dia = agregar_meta_por_dia(sh)
    leads_por_dia = ler_leads_por_dia(sh)
    linhas = montar_tabela(meta_por_dia, leads_por_dia)
    escrever_saida(sh, linhas)
    print(f"{len(linhas) - 1} dias processados na aba '{ABA_SAIDA}'.")


if __name__ == "__main__":
    main()
