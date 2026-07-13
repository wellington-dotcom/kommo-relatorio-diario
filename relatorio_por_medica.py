"""
relatorio_por_medica.py

Cria/atualiza uma aba "Gasto e Leads por Médica" cruzando:
  - Gasto diário por médica, identificado automaticamente pelo nome que
    já aparece no nome da campanha/anúncio na aba "Meta Ads Report"
    (ex: "[ISABELLA]", "[DR_BRUNA]", "[LAESSA]")
  - Uma coluna "Leads" que fica em aberto pra preenchimento manual (hoje
    essa contagem por médica vem do Marcelo por WhatsApp)
  - Uma coluna "Custo por Lead" calculada automaticamente (fórmula viva
    na planilha) assim que Leads for preenchido

IMPORTANTE: este script NUNCA apaga a aba inteira. Ele só:
  - adiciona linhas novas pra combinações (data, médica) que ainda não
    existem
  - atualiza o valor de gasto de linhas que já existem
  - nunca escreve nem apaga nada na coluna "Leads" preenchida manualmente

Reaproveita os secrets que já existem: GOOGLE_CREDENTIALS e SHEET_ID.
"""

import os
import json
import time
from collections import defaultdict
import gspread


def com_retry(func, *args, tentativas=4, espera_inicial=5, **kwargs):
    """Roda func(*args, **kwargs) tentando de novo se der erro transitório
    da API do Google (ex: 503 Service Unavailable). Espera aumenta a cada
    tentativa (5s, 10s, 20s...)."""
    espera = espera_inicial
    for tentativa in range(1, tentativas + 1):
        try:
            return func(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            transitorio = status in (429, 500, 502, 503, 504)
            if not transitorio or tentativa == tentativas:
                raise
            print(
                f"Aviso: erro transitório da API do Google (tentativa "
                f"{tentativa}/{tentativas}): {e}. Tentando de novo em {espera}s..."
            )
            time.sleep(espera)
            espera *= 2

GOOGLE_CREDENTIALS = json.loads(os.environ["GOOGLE_CREDENTIALS"])
SHEET_ID = os.environ["SHEET_ID"]

ABA_META_ADS = "Meta Ads Report"
ABA_LEADS_KOMMO = "Leads Novos por Dia"
ABA_SAIDA = "Gasto e Leads por Médica"

COL_DATA_META = "date_start"
COL_SPEND = "spend"
COL_CAMPANHA = "campaign_name"
COL_ANUNCIO = "ad_name"

# AJUSTAR conforme as colunas reais da aba "Leads Novos por Dia":
# qual letra de coluna tem a Data, e qual tem a quantidade de leads.
COLUNA_DATA_KOMMO = "A"
COLUNA_LEADS_KOMMO = "B"

LABEL_TOTAL_DIA = "TOTAL DO DIA (Kommo)"

# Palavras-chave que identificam cada médica dentro do nome da
# campanha/anúncio (não diferencia maiúsculas/minúsculas). Ajuste ou
# adicione médicas aqui conforme necessário.
MEDICAS = {
    "Dra. Isabella Eleutério": ["ISABELLA"],
    "Dra. Bruna": ["BRUNA"],
    "Dra. Laessa": ["LAESSA"],
}

HEADER = [
    "Data",
    "Médica",
    "Gasto Total (Meta)",
    "Leads (preencher manualmente)",
    "Custo por Lead",
]


def conectar_planilha():
    gc = gspread.service_account_from_dict(GOOGLE_CREDENTIALS)
    return com_retry(gc.open_by_key, SHEET_ID)


def identificar_medica(texto):
    texto_up = (texto or "").upper()
    for medica, palavras in MEDICAS.items():
        if any(p in texto_up for p in palavras):
            return medica
    return None


def agregar_gasto_por_medica_dia(sh):
    ws = sh.worksheet(ABA_META_ADS)
    registros = com_retry(ws.get_all_records)

    por_chave = defaultdict(float)  # (data, medica) -> gasto acumulado
    nao_identificados = set()

    for row in registros:
        data = row.get(COL_DATA_META)
        if not data:
            continue
        texto_busca = f"{row.get(COL_CAMPANHA, '')} {row.get(COL_ANUNCIO, '')}"
        medica = identificar_medica(texto_busca)
        if not medica:
            nao_identificados.add(texto_busca[:60])
            continue
        try:
            spend = float(row.get(COL_SPEND) or 0)
        except (TypeError, ValueError):
            spend = 0.0
        por_chave[(data, medica)] += spend

    return por_chave, nao_identificados


def verificar_aba_leads_kommo(sh):
    """Só confirma que a aba existe, pra avisar se o nome estiver errado.
    O valor em si passa a ser buscado por fórmula direto na planilha."""
    try:
        sh.worksheet(ABA_LEADS_KOMMO)
        return True
    except gspread.exceptions.WorksheetNotFound:
        print(f"Aviso: aba '{ABA_LEADS_KOMMO}' não encontrada - a fórmula de total do dia vai ficar em branco.")
        return False


def garantir_aba_saida(sh):
    try:
        ws = sh.worksheet(ABA_SAIDA)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=ABA_SAIDA, rows=1000, cols=len(HEADER) + 2)
        ws.update([HEADER], "A1")
        ws.freeze(rows=1)
    return ws


def ler_linhas_existentes(ws):
    """Retorna {(data, medica): numero_da_linha} e a lista completa de linhas."""
    valores = com_retry(ws.get_all_values)
    existentes = {}
    for i, linha in enumerate(valores[1:], start=2):  # pula cabeçalho
        if len(linha) >= 2 and linha[0] and linha[1]:
            existentes[(linha[0], linha[1])] = i
    return existentes, valores


def main():
    sh = conectar_planilha()
    ws = garantir_aba_saida(sh)

    gasto_por_chave, nao_identificados = agregar_gasto_por_medica_dia(sh)
    verificar_aba_leads_kommo(sh)
    existentes, valores = ler_linhas_existentes(ws)

    gasto_total_por_dia = defaultdict(float)
    medicas_por_data = defaultdict(set)
    for (data, medica), gasto in gasto_por_chave.items():
        gasto_total_por_dia[data] += gasto
        medicas_por_data[data].add(medica)

    atualizacoes = []
    novas_linhas = []
    proxima_linha = len(valores) + 1

    def formula_leads_kommo(linha_num):
        intervalo = (
            f"'{ABA_LEADS_KOMMO}'!{COLUNA_DATA_KOMMO}:{COLUNA_LEADS_KOMMO}"
        )
        col_offset = ord(COLUNA_LEADS_KOMMO) - ord(COLUNA_DATA_KOMMO) + 1
        return f'=IFERROR(VLOOKUP(A{linha_num},{intervalo},{col_offset},FALSE),"")'

    def processar_linha(data, medica, gasto, eh_total=False):
        """eh_total=True -> linha de TOTAL, também escreve a fórmula de
        leads. eh_total=False -> linha por médica, nunca mexe em Leads."""
        nonlocal proxima_linha
        gasto_fmt = round(gasto, 2)
        chave = (data, medica)

        if chave in existentes:
            linha_num = existentes[chave]
            if eh_total:
                faixa = f"C{linha_num}:D{linha_num}"
                valores_att = [gasto_fmt, formula_leads_kommo(linha_num)]
            else:
                faixa = f"C{linha_num}"
                valores_att = [gasto_fmt]
            atualizacoes.append({"range": faixa, "values": [valores_att]})
        else:
            linha_num = proxima_linha + len(novas_linhas)
            formula_custo = f'=IF(D{linha_num}="","",C{linha_num}/D{linha_num})'
            leads_valor = formula_leads_kommo(linha_num) if eh_total else ""
            novas_linhas.append([data, medica, gasto_fmt, leads_valor, formula_custo])

    # Ordem fixa das médicas (mesma ordem do dicionário MEDICAS), pra sair
    # sempre no mesmo padrão visual, dia a dia.
    ordem_medicas = list(MEDICAS.keys())

    for data in sorted(gasto_total_por_dia.keys()):
        for medica in ordem_medicas:
            if medica in medicas_por_data[data]:
                processar_linha(data, medica, gasto_por_chave[(data, medica)])
        processar_linha(data, LABEL_TOTAL_DIA, gasto_total_por_dia[data], eh_total=True)

    if atualizacoes:
        com_retry(ws.batch_update, atualizacoes, value_input_option="USER_ENTERED")

    if novas_linhas:
        com_retry(ws.append_rows, novas_linhas, value_input_option="USER_ENTERED")

    print(
        f"{len(atualizacoes)} linha(s) atualizadas, "
        f"{len(novas_linhas)} linha(s) nova(s) adicionadas."
    )
    if nao_identificados:
        print(
            f"\n{len(nao_identificados)} combinação(ões) de campanha/anúncio "
            f"sem médica identificada (gasto não incluído no relatório):"
        )
        for texto in list(nao_identificados)[:10]:
            print(f"  - {texto}")


if __name__ == "__main__":
    main()
