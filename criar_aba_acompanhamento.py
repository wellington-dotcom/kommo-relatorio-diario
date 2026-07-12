import os
import json

import gspread
from google.oauth2.service_account import Credentials

SHEET_ID = os.environ["SHEET_ID"]
GOOGLE_CREDS = os.environ["GOOGLE_CREDENTIALS"]

BRUTO = "'Números Brutos Diários'"
PESSOA_SHEET = "'Relatório por Pessoa'"
LEADS_NOVOS = "'Leads Novos por Dia'"
TEMPO_RESPOSTA = "'Tempo de Resposta'"

PESSOAS = ["Rayana", "Isadora", "Ana Lívia", "Isabella Eleutério"]

AZUL_ESCURO = {"red": 0.12, "green": 0.31, "blue": 0.47}
AZUL_CLARO = {"red": 0.85, "green": 0.88, "blue": 0.95}
VERDE_CLARO = {"red": 0.85, "green": 0.94, "blue": 0.85}
BRANCO = {"red": 1, "green": 1, "blue": 1}

scopes = ["https://www.googleapis.com/auth/spreadsheets"]
creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS), scopes=scopes)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SHEET_ID)


def set_locale(codigo_locale):
    sh.batch_update(
        {
            "requests": [
                {
                    "updateSpreadsheetProperties": {
                        "properties": {"locale": codigo_locale},
                        "fields": "locale",
                    }
                }
            ]
        }
    )


def get_ou_recriar_ws(titulo, linhas=100, colunas=10):
    try:
        ws = sh.worksheet(titulo)
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=titulo, rows=linhas, cols=colunas)
    return ws


def gravar(ws, celulas):
    lote = [{"range": k, "values": [[v]]} for k, v in celulas.items()]
    ws.batch_update(lote, value_input_option="USER_ENTERED")


def col_letra(indice_zero):
    letras = ""
    n = indice_zero + 1
    while n > 0:
        n, resto = divmod(n - 1, 26)
        letras = chr(65 + resto) + letras
    return letras


try:
    metadata = sh.fetch_sheet_metadata()
    locale_original = metadata.get("properties", {}).get("locale", "pt_BR")
except Exception:
    locale_original = "pt_BR"

print(f"Locale original detectado: {locale_original}")
print("Trocando locale para en_US temporariamente...")
set_locale("en_US")

try:
    ws = get_ou_recriar_ws("Acompanhamento do Dia")

    celulas = {
        "A1": "ACOMPANHAMENTO DO DIA",
        "A2": '=CONCATENATE("Dados de: ",TEXT(TODAY(),"dd/mm/yyyy"))',
        "A3": (
            "Esta aba mostra sempre o dia de HOJE automaticamente — não precisa editar nada. "
            "Atualiza sozinha a cada 15 minutos junto com o robô."
        ),
        "A5": "NÚMEROS GERAIS DE HOJE",
        "A6": "Métrica",
        "B6": "Valor",
        "A7": "Leads (novos no funil)",
        "B7": f'=IFERROR(SUMIFS({LEADS_NOVOS}!B:B,{LEADS_NOVOS}!A:A,TODAY()),0)',
        "A8": "Ações",
        "B8": f'=IFERROR(SUMIFS({BRUTO}!C:C,{BRUTO}!A:A,TODAY()),0)',
        "A9": "Agendamentos",
        "B9": f'=IFERROR(SUMIFS({BRUTO}!D:D,{BRUTO}!A:A,TODAY()),0)',
        "A10": "Compareceu",
        "B10": f'=IFERROR(SUMIFS({BRUTO}!E:E,{BRUTO}!A:A,TODAY()),0)',
        "A11": "Faltou",
        "B11": f'=IFERROR(SUMIFS({BRUTO}!F:F,{BRUTO}!A:A,TODAY()),0)',
        "A13": "POR PESSOA (HOJE)",
    }

    headers_pessoa = ["Pessoa", "Leads", "Ações", "Agendamentos", "Compareceu", "Faltou"]
    for i, h in enumerate(headers_pessoa):
        celulas[f"{col_letra(i)}14"] = h

    for idx, pessoa in enumerate(PESSOAS):
        row = 15 + idx
        a = f"A{row}"
        celulas[f"A{row}"] = pessoa
        celulas[f"B{row}"] = (
            f'=IFERROR(SUMIFS({PESSOA_SHEET}!C:C,{PESSOA_SHEET}!A:A,TODAY(),{PESSOA_SHEET}!B:B,{a}),0)'
        )
        celulas[f"C{row}"] = (
            f'=IFERROR(SUMIFS({PESSOA_SHEET}!D:D,{PESSOA_SHEET}!A:A,TODAY(),{PESSOA_SHEET}!B:B,{a}),0)'
        )
        celulas[f"D{row}"] = (
            f'=IFERROR(SUMIFS({PESSOA_SHEET}!E:E,{PESSOA_SHEET}!A:A,TODAY(),{PESSOA_SHEET}!B:B,{a}),0)'
        )
        celulas[f"E{row}"] = (
            f'=IFERROR(SUMIFS({PESSOA_SHEET}!F:F,{PESSOA_SHEET}!A:A,TODAY(),{PESSOA_SHEET}!B:B,{a}),0)'
        )
        celulas[f"F{row}"] = (
            f'=IFERROR(SUMIFS({PESSOA_SHEET}!G:G,{PESSOA_SHEET}!A:A,TODAY(),{PESSOA_SHEET}!B:B,{a}),0)'
        )

    celulas["A20"] = "TEMPO DE RESPOSTA (HOJE, 7h-19h)"
    headers_tempo = ["Pessoa", "Primeira Resposta (min)", "Qtd", "Resposta Média (min)", "Qtd"]
    for i, h in enumerate(headers_tempo):
        celulas[f"{col_letra(i)}21"] = h

    for idx, pessoa in enumerate(PESSOAS):
        row = 22 + idx
        a = f"A{row}"
        celulas[f"A{row}"] = pessoa
        celulas[f"B{row}"] = (
            f'=IFERROR(SUMIFS({TEMPO_RESPOSTA}!C:C,{TEMPO_RESPOSTA}!A:A,TODAY(),{TEMPO_RESPOSTA}!B:B,{a}),0)'
        )
        celulas[f"C{row}"] = (
            f'=IFERROR(SUMIFS({TEMPO_RESPOSTA}!D:D,{TEMPO_RESPOSTA}!A:A,TODAY(),{TEMPO_RESPOSTA}!B:B,{a}),0)'
        )
        celulas[f"D{row}"] = (
            f'=IFERROR(SUMIFS({TEMPO_RESPOSTA}!E:E,{TEMPO_RESPOSTA}!A:A,TODAY(),{TEMPO_RESPOSTA}!B:B,{a}),0)'
        )
        celulas[f"E{row}"] = (
            f'=IFERROR(SUMIFS({TEMPO_RESPOSTA}!F:F,{TEMPO_RESPOSTA}!A:A,TODAY(),{TEMPO_RESPOSTA}!B:B,{a}),0)'
        )

    celulas["A27"] = (
        "Números de hoje, atualizados automaticamente. Para históricos e outros períodos, "
        "veja as abas 'Resumo do Mês' e 'Resumo de Eventos por Período'."
    )

    gravar(ws, celulas)

    ws.format("A1:F1", {"backgroundColor": AZUL_ESCURO, "textFormat": {"bold": True, "foregroundColor": BRANCO, "fontSize": 12}})
    ws.format("A2", {"textFormat": {"bold": True}})
    ws.format("A5:B5", {"backgroundColor": AZUL_ESCURO, "textFormat": {"bold": True, "foregroundColor": BRANCO}})
    ws.format("A6:B6", {"textFormat": {"bold": True}, "backgroundColor": AZUL_CLARO})
    ws.format("A13:F13", {"backgroundColor": AZUL_ESCURO, "textFormat": {"bold": True, "foregroundColor": BRANCO}})
    ws.format("A14:F14", {"textFormat": {"bold": True}, "backgroundColor": AZUL_CLARO})
    ws.format("A20:F20", {"backgroundColor": AZUL_ESCURO, "textFormat": {"bold": True, "foregroundColor": BRANCO}})
    ws.format("A21:E21", {"textFormat": {"bold": True}, "backgroundColor": AZUL_CLARO})
    ws.format("A7:B11", {"backgroundColor": VERDE_CLARO})

    ws.columns_auto_resize(0, 6)

    print("Aba 'Acompanhamento do Dia' criada com sucesso.")

finally:
    print(f"Devolvendo o locale da planilha para {locale_original}...")
    set_locale(locale_original)

print("Pronto — confira a aba na planilha.")
