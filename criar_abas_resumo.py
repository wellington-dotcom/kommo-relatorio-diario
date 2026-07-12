import os
import json

import gspread
from google.oauth2.service_account import Credentials

SHEET_ID = os.environ["SHEET_ID"]
GOOGLE_CREDS = os.environ["GOOGLE_CREDENTIALS"]

BRUTO = "'Números Brutos Diários'"
PESSOA_SHEET = "'Relatório por Pessoa'"
LEADS_NOVOS = "'Leads Novos por Dia'"
MIX = "'Mix de Tipos por Pessoa'"

LAST = 2000  # faixa das fórmulas (dá margem de anos de dados diários)
PESSOAS = ["Rayana", "Isadora", "Ana Lívia", "Isabella Eleutério"]

TIPOS_FIXOS = [
    "Lead criado", "Lead excluído", "Mudança de etapa", "Nome alterado",
    "Responsável alterado", "Tag adicionada", "Tag removida",
    "Vinculado a outro registro", "Desvinculado de registro", "Registros mesclados",
    "Mensagem direta", "Mensagem recebida (chat)", "Mensagem enviada (chat)",
    "Conversa respondida", "Conversa atribuída", "Conversa iniciada",
    "Conversa encerrada", "Conversa excluída", "Nota adicionada", "Nota excluída",
    "Tarefa criada", "Tarefa excluída", "Tarefa concluída",
    "Descrição da tarefa alterada", "Tipo de tarefa alterado",
    "Prazo da tarefa alterado", "Valor do negócio alterado",
    "Empresa cadastrada", "Empresa excluída", "Contato cadastrado", "Contato excluído",
]

AZUL_ESCURO = {"red": 0.12, "green": 0.31, "blue": 0.47}
AZUL_CLARO = {"red": 0.85, "green": 0.88, "blue": 0.95}
AMARELO = {"red": 1, "green": 0.95, "blue": 0.8}
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


def get_ou_recriar_ws(titulo, linhas=200, colunas=10):
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


INICIO = "$B$2"
FIM = "$E$2"

# ============================================================
# Descobre o locale atual (pra devolver do jeito que estava)
# ============================================================
try:
    metadata = sh.fetch_sheet_metadata()
    locale_original = metadata.get("properties", {}).get("locale", "pt_BR")
except Exception:
    locale_original = "pt_BR"  # fallback razoável, já que a planilha é em português

print(f"Locale original detectado: {locale_original}")
print("Trocando locale para en_US temporariamente (pra escrever as fórmulas em inglês)...")
set_locale("en_US")

try:
    # ============================================================
    # ABA: Resumo do Mês (recriada, com Leads corrigido)
    # ============================================================
    ws = get_ou_recriar_ws("Resumo do Mês")

    celulas = {
        "A1": "RESUMO DO PERÍODO",
        "A2": "Data início:",
        "B2": "2026-07-01",
        "D2": "Data fim:",
        "E2": "2026-07-31",
        "A3": "Edite as duas datas acima (fundo amarelo) para mudar o período — tudo abaixo recalcula sozinho.",
        "A5": "RESUMO GERAL",
    }

    headers1 = ["Métrica", "Total", "Média/dia", "Melhor dia", "Valor", "Pior dia", "Valor"]
    for i, h in enumerate(headers1):
        celulas[f"{col_letra(i)}6"] = h

    metricas = [
        ("Leads (novos no funil)", LEADS_NOVOS, "A", "B"),
        ("Ações", BRUTO, "A", "C"),
        ("Agendamentos", BRUTO, "A", "D"),
        ("Compareceu", BRUTO, "A", "E"),
        ("Faltou", BRUTO, "A", "F"),
    ]

    for idx, (nome, sheet, dcol, vcol) in enumerate(metricas):
        row = 7 + idx
        data_rng = f"{sheet}!${dcol}$2:${dcol}${1 + LAST}"
        val_rng = f"{sheet}!${vcol}$2:${vcol}${1 + LAST}"
        celulas[f"A{row}"] = nome
        celulas[f"B{row}"] = f'=SUMIFS({val_rng},{data_rng},">="&{INICIO},{data_rng},"<="&{FIM})'
        celulas[f"C{row}"] = f'=IFERROR(AVERAGEIFS({val_rng},{data_rng},">="&{INICIO},{data_rng},"<="&{FIM}),0)'
        celulas[f"E{row}"] = f'=IFERROR(MAXIFS({val_rng},{data_rng},">="&{INICIO},{data_rng},"<="&{FIM}),0)'
        match_melhor = f'MATCH(1,INDEX(({val_rng}=E{row})*({data_rng}>={INICIO})*({data_rng}<={FIM}),0),0)'
        celulas[f"D{row}"] = f'=IF(E{row}=0,"",IFERROR(TEXT(INDEX({data_rng},{match_melhor}),"yyyy-mm-dd"),""))'
        celulas[f"G{row}"] = f'=IFERROR(MINIFS({val_rng},{data_rng},">="&{INICIO},{data_rng},"<="&{FIM}),0)'
        match_pior = f'MATCH(1,INDEX(({val_rng}=G{row})*({data_rng}>={INICIO})*({data_rng}<={FIM}),0),0)'
        celulas[f"F{row}"] = f'=IF(G{row}=0,"",IFERROR(TEXT(INDEX({data_rng},{match_pior}),"yyyy-mm-dd"),""))'

    celulas["A13"] = "MÉDIA DE TRABALHO POR PESSOA (no período)"
    headers2 = ["Pessoa", "Dias Ativos", "Ações Totais", "Média Ações/Dia", "Agendamentos Totais", "Média Agendamentos/Dia"]
    for i, h in enumerate(headers2):
        celulas[f"{col_letra(i)}14"] = h

    pessoa_col = f"{PESSOA_SHEET}!$B$2:$B${1 + LAST}"
    data_col_pessoa = f"{PESSOA_SHEET}!$A$2:$A${1 + LAST}"
    acoes_col = f"{PESSOA_SHEET}!$D$2:$D${1 + LAST}"
    agend_col = f"{PESSOA_SHEET}!$E$2:$E${1 + LAST}"

    for idx, pessoa in enumerate(PESSOAS):
        row = 15 + idx
        a = f"A{row}"
        celulas[f"A{row}"] = pessoa
        celulas[f"B{row}"] = (
            f'=COUNTIFS({pessoa_col},{a},{acoes_col},">0",'
            f'{data_col_pessoa},">="&{INICIO},{data_col_pessoa},"<="&{FIM})'
        )
        celulas[f"C{row}"] = (
            f'=SUMIFS({acoes_col},{pessoa_col},{a},'
            f'{data_col_pessoa},">="&{INICIO},{data_col_pessoa},"<="&{FIM})'
        )
        celulas[f"D{row}"] = f"=IFERROR(C{row}/B{row},0)"
        celulas[f"E{row}"] = (
            f'=SUMIFS({agend_col},{pessoa_col},{a},'
            f'{data_col_pessoa},">="&{INICIO},{data_col_pessoa},"<="&{FIM})'
        )
        celulas[f"F{row}"] = f"=IFERROR(E{row}/B{row},0)"

    celulas["A20"] = (
        "Fórmulas puxam de 'Leads Novos por Dia' (Leads), 'Números Brutos Diários' "
        "(Ações/Agendamentos/Compareceu/Faltou) e 'Relatório por Pessoa' (por pessoa)."
    )

    gravar(ws, celulas)

    ws.format("A1:G1", {"backgroundColor": AZUL_ESCURO, "textFormat": {"bold": True, "foregroundColor": BRANCO}})
    ws.format("A6:G6", {"textFormat": {"bold": True}, "backgroundColor": AZUL_CLARO})
    ws.format("A13:F13", {"backgroundColor": AZUL_ESCURO, "textFormat": {"bold": True, "foregroundColor": BRANCO}})
    ws.format("A14:F14", {"textFormat": {"bold": True}, "backgroundColor": AZUL_CLARO})
    ws.format("B2", {"backgroundColor": AMARELO, "numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}})
    ws.format("E2", {"backgroundColor": AMARELO, "numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}})

    print("Aba 'Resumo do Mês' recriada com sucesso (Leads corrigido).")

    # ============================================================
    # ABA: Resumo de Eventos por Período (nova)
    # ============================================================
    ws2 = get_ou_recriar_ws("Resumo de Eventos por Período")

    data_rng2 = f"{MIX}!$A$2:$A${1 + LAST}"
    pessoa_rng2 = f"{MIX}!$B$2:$B${1 + LAST}"
    tipo_rng2 = f"{MIX}!$C$2:$C${1 + LAST}"
    qtd_rng2 = f"{MIX}!$D$2:$D${1 + LAST}"

    celulas2 = {
        "A1": "RESUMO DE EVENTOS POR PERÍODO",
        "A2": "Data início:",
        "B2": "2026-07-01",
        "D2": "Data fim:",
        "E2": "2026-07-31",
        "A3": "Edite as duas datas acima (fundo amarelo) para mudar o período — tudo abaixo recalcula sozinho.",
    }

    headers = ["Tipo de Evento", "Total"] + PESSOAS
    for i, h in enumerate(headers):
        celulas2[f"{col_letra(i)}5"] = h

    primeira_linha_tipo = 6
    for i, tipo in enumerate(TIPOS_FIXOS):
        row = primeira_linha_tipo + i
        tipo_cell = f"A{row}"
        celulas2[f"A{row}"] = tipo
        celulas2[f"B{row}"] = (
            f'=SUMIFS({qtd_rng2},{data_rng2},">="&{INICIO},{data_rng2},"<="&{FIM},'
            f'{tipo_rng2},{tipo_cell})'
        )
        for j, pessoa in enumerate(PESSOAS):
            col = col_letra(2 + j)
            celulas2[f"{col}{row}"] = (
                f'=SUMIFS({qtd_rng2},{data_rng2},">="&{INICIO},{data_rng2},"<="&{FIM},'
                f'{tipo_rng2},{tipo_cell},{pessoa_rng2},"{pessoa}")'
            )

    linha_custom = primeira_linha_tipo + len(TIPOS_FIXOS)
    linha_auxiliar = linha_custom + 1
    linha_totalgeral = linha_custom + 3

    celulas2[f"A{linha_auxiliar}"] = "(auxiliar) Total Geral no período"
    celulas2[f"B{linha_auxiliar}"] = f'=SUMIFS({qtd_rng2},{data_rng2},">="&{INICIO},{data_rng2},"<="&{FIM})'
    for j, pessoa in enumerate(PESSOAS):
        col = col_letra(2 + j)
        celulas2[f"{col}{linha_auxiliar}"] = (
            f'=SUMIFS({qtd_rng2},{data_rng2},">="&{INICIO},{data_rng2},"<="&{FIM},'
            f'{pessoa_rng2},"{pessoa}")'
        )

    celulas2[f"A{linha_custom}"] = "Campos personalizados (todos)"
    for col in ["B", "C", "D", "E", "F"]:
        celulas2[f"{col}{linha_custom}"] = f"={col}{linha_auxiliar}-SUM({col}{primeira_linha_tipo}:{col}{linha_custom - 1})"

    celulas2[f"A{linha_totalgeral}"] = "TOTAL GERAL"
    for col in ["B", "C", "D", "E", "F"]:
        celulas2[f"{col}{linha_totalgeral}"] = f"=SUM({col}{primeira_linha_tipo}:{col}{linha_custom})"

    celulas2[f"A{linha_totalgeral + 2}"] = (
        "Fórmulas puxam de 'Mix de Tipos por Pessoa'. 'Campos personalizados' agrupa "
        "qualquer tipo que não seja fixo do sistema Kommo (não quebra se surgir campo novo)."
    )

    gravar(ws2, celulas2)

    ws2.format("A1:F1", {"backgroundColor": AZUL_ESCURO, "textFormat": {"bold": True, "foregroundColor": BRANCO}})
    ws2.format("A5:F5", {"textFormat": {"bold": True}, "backgroundColor": AZUL_CLARO})
    ws2.format("B2", {"backgroundColor": AMARELO, "numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}})
    ws2.format("E2", {"backgroundColor": AMARELO, "numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}})
    ws2.format(f"A{linha_custom}:F{linha_custom}", {"textFormat": {"bold": True}, "backgroundColor": AMARELO})
    ws2.format(f"A{linha_totalgeral}:F{linha_totalgeral}", {"textFormat": {"bold": True}, "backgroundColor": AZUL_CLARO})

    print("Aba 'Resumo de Eventos por Período' criada com sucesso.")

finally:
    print(f"Devolvendo o locale da planilha para {locale_original}...")
    set_locale(locale_original)

print("Tudo pronto — confira as duas abas na planilha.")
