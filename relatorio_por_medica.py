"""
relatorio_por_medica.py

Cria/mantém uma aba "Gasto e Leads por Médica" com a estrutura de linhas
(uma por médica/dia + uma linha de TOTAL DO DIA), mas os VALORES em si
são fórmulas vivas da própria planilha, não números calculados pelo
script. Ou seja: o script só garante que a linha existe; quem calcula o
valor é o próprio Google Sheets, puxando das outras abas.

Fórmulas usadas:
  - Gasto Total (Meta) e Leads Entregues (Meta): SUMPRODUCT que soma a
    aba "Meta Ads Report" filtrando por data e, nas linhas de médica,
    também pelo nome dela aparecer no nome da campanha/anúncio. As
    colunas de origem são localizadas pelo NOME do cabeçalho (MATCH),
    não por letra fixa - evita erro de contar posição de coluna errado.
  - Leads Reais (Kommo): VLOOKUP na aba "Leads Novos por Dia".
  - Custo por Lead: gasto do dia dividido pelos leads reais do dia.

Colunas da aba de saída:
  A: Data
  B: Médica  (ou "TOTAL DO DIA" na linha de resumo)
  C: Gasto Total (Meta)        <- fórmula
  D: Leads Entregues (Meta)    <- fórmula
  E: Leads Reais (Kommo)       <- fórmula, só na linha de TOTAL DO DIA
  F: Custo por Lead            <- fórmula, só na linha de TOTAL DO DIA

IMPORTANTE: este script NUNCA apaga a aba inteira. Ele só adiciona linhas
novas pra combinações (data, médica) que ainda não existem, e garante que
as fórmulas de C, D (e E, F na linha de total) estejam corretas em todas
as linhas - inclusive corrigindo linhas antigas se necessário.

Reaproveita os secrets que já existem: GOOGLE_CREDENTIALS e SHEET_ID.
"""

import os
import json
import time
from collections import defaultdict
import gspread


def com_retry(func, *args, tentativas=4, espera_inicial=5, **kwargs):
    """Roda func(*args, **kwargs) tentando de novo se der erro transitório
    da API do Google (ex: 503 Service Unavailable)."""
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

# Nomes EXATOS dos cabeçalhos na aba "Meta Ads Report" (a fórmula acha a
# coluna procurando por esse texto na linha 1, não por letra fixa).
COL_DATA_META = "date_start"
COL_SPEND = "spend"
COL_CAMPANHA = "campaign_name"
COL_ANUNCIO = "ad_name"
COL_LEADS_META = "actions__onsite_conversion.messaging_conversation_started_7d"

# Faixa de colunas na aba Meta Ads Report a considerar na busca por nome
# de cabeçalho (ZZ cobre até ~700 colunas, bem mais que o necessário).
FAIXA_META_ADS = f"'{ABA_META_ADS}'!$A:$ZZ"
CABECALHO_META_ADS = f"'{ABA_META_ADS}'!$1:$1"

# AJUSTAR conforme as colunas reais da aba "Leads Novos por Dia":
COLUNA_DATA_KOMMO = "A"
COLUNA_LEADS_KOMMO = "B"

LABEL_TOTAL_DIA = "TOTAL DO DIA"
LABEL_TOTAL_DIA_ANTIGO = "TOTAL DO DIA (Kommo)"  # rótulo usado em versão anterior

# Palavras-chave que identificam cada médica dentro do nome da
# campanha/anúncio (não diferencia maiúsculas/minúsculas).
MEDICAS = {
    "Dra. Isabella Eleutério": ["ISABELLA"],
    "Dra. Bruna": ["BRUNA"],
    "Dra. Laessa": ["LAESSA"],
}

HEADER = [
    "Data",
    "Médica",
    "Gasto Total (Meta)",
    "Leads Entregues (Meta)",
    "Leads Reais (Kommo)",
    "Custo por Lead",
]


def conectar_planilha():
    gc = gspread.service_account_from_dict(GOOGLE_CREDENTIALS)
    return com_retry(gc.open_by_key, SHEET_ID)


def col_por_nome(nome_cabecalho):
    """Fórmula parcial que localiza uma coluna inteira da aba Meta Ads
    Report pelo nome do cabeçalho, em vez de uma letra fixa."""
    return f'INDEX({FAIXA_META_ADS},0,MATCH("{nome_cabecalho}",{CABECALHO_META_ADS},0))'


def formula_meta_sumproduct(linha_num, coluna_valor, palavras_chave=None):
    """Soma uma coluna da aba Meta Ads Report filtrando por data (sempre)
    e, se palavras_chave for passado, também pelo nome da médica aparecer
    no nome da campanha/anúncio (usado nas linhas por médica). Sem
    palavras_chave, soma o dia inteiro (usado na linha de TOTAL DO DIA)."""
    filtro_data = f"({col_por_nome(COL_DATA_META)}=$A{linha_num})"
    valor = f"N({col_por_nome(coluna_valor)})"

    if palavras_chave:
        texto_busca = f"{col_por_nome(COL_CAMPANHA)}&\" \"&{col_por_nome(COL_ANUNCIO)}"
        partes = [f'ISNUMBER(SEARCH("{p}",{texto_busca}))' for p in palavras_chave]
        filtro_medica = "(" + "+".join(partes) + ">0)"
        return f"=SUMPRODUCT({filtro_data}*{filtro_medica}*{valor})"

    return f"=SUMPRODUCT({filtro_data}*{valor})"


def formula_leads_kommo(linha_num):
    intervalo = f"'{ABA_LEADS_KOMMO}'!{COLUNA_DATA_KOMMO}:{COLUNA_LEADS_KOMMO}"
    col_offset = ord(COLUNA_LEADS_KOMMO) - ord(COLUNA_DATA_KOMMO) + 1
    return f'=IFERROR(VLOOKUP(A{linha_num};{intervalo};{col_offset};FALSE);"")'


def formula_custo(linha_num):
    return f'=IF(E{linha_num}="";"";C{linha_num}/E{linha_num})'


def identificar_medica(texto):
    texto_up = (texto or "").upper()
    for medica, palavras in MEDICAS.items():
        if any(p in texto_up for p in palavras):
            return medica
    return None


def mapear_datas_e_medicas(sh):
    """Só descobre QUAIS combinações (data, médica) existem - não soma
    valor nenhum, já que os valores agora são fórmulas na planilha."""
    ws = sh.worksheet(ABA_META_ADS)
    registros = com_retry(ws.get_all_records)

    medicas_por_data = defaultdict(set)
    nao_identificados = set()

    for row in registros:
        data = row.get(COL_DATA_META)
        if not data:
            continue
        texto_busca = f"{row.get(COL_CAMPANHA, '')} {row.get(COL_ANUNCIO, '')}"
        medica = identificar_medica(texto_busca)
        if medica:
            medicas_por_data[data].add(medica)
        else:
            nao_identificados.add(texto_busca[:60])

    return medicas_por_data, nao_identificados


def verificar_aba_leads_kommo(sh):
    try:
        sh.worksheet(ABA_LEADS_KOMMO)
    except gspread.exceptions.WorksheetNotFound:
        print(f"Aviso: aba '{ABA_LEADS_KOMMO}' não encontrada - a fórmula de leads reais vai ficar em branco.")


def garantir_aba_saida(sh):
    try:
        ws = sh.worksheet(ABA_SAIDA)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=ABA_SAIDA, rows=1000, cols=len(HEADER) + 2)
        ws.update([HEADER], "A1")
        ws.freeze(rows=1)
    return ws


def migrar_rotulo_antigo(ws):
    valores = com_retry(ws.get_all_values)
    atualizacoes = []
    for i, linha in enumerate(valores[1:], start=2):
        if len(linha) >= 2 and linha[1] == LABEL_TOTAL_DIA_ANTIGO:
            atualizacoes.append({"range": f"B{i}", "values": [[LABEL_TOTAL_DIA]]})
    if atualizacoes:
        com_retry(ws.batch_update, atualizacoes, value_input_option="USER_ENTERED")
        print(f"Migrado {len(atualizacoes)} linha(s) do rótulo antigo pro novo.")


def ler_linhas_existentes(ws):
    valores = com_retry(ws.get_all_values)
    existentes = {}
    for i, linha in enumerate(valores[1:], start=2):
        if len(linha) >= 2 and linha[0] and linha[1]:
            existentes[(linha[0], linha[1])] = i
    return existentes, valores


def main():
    sh = conectar_planilha()
    ws = garantir_aba_saida(sh)

    medicas_por_data, nao_identificados = mapear_datas_e_medicas(sh)
    verificar_aba_leads_kommo(sh)
    migrar_rotulo_antigo(ws)
    existentes, valores = ler_linhas_existentes(ws)

    atualizacoes = []
    novas_linhas = []
    proxima_linha = len(valores) + 1

    def processar_linha(data, medica, eh_total=False):
        nonlocal proxima_linha
        chave = (data, medica)
        palavras = None if eh_total else MEDICAS[medica]

        if chave in existentes:
            linha_num = existentes[chave]
            f_gasto = formula_meta_sumproduct(linha_num, COL_SPEND, palavras)
            f_leads_meta = formula_meta_sumproduct(linha_num, COL_LEADS_META, palavras)
            if eh_total:
                atualizacoes.append({
                    "range": f"C{linha_num}:F{linha_num}",
                    "values": [[
                        f_gasto, f_leads_meta,
                        formula_leads_kommo(linha_num), formula_custo(linha_num),
                    ]],
                })
            else:
                atualizacoes.append({
                    "range": f"C{linha_num}:D{linha_num}",
                    "values": [[f_gasto, f_leads_meta]],
                })
        else:
            linha_num = proxima_linha + len(novas_linhas)
            f_gasto = formula_meta_sumproduct(linha_num, COL_SPEND, palavras)
            f_leads_meta = formula_meta_sumproduct(linha_num, COL_LEADS_META, palavras)
            if eh_total:
                novas_linhas.append([
                    data, medica, f_gasto, f_leads_meta,
                    formula_leads_kommo(linha_num), formula_custo(linha_num),
                ])
            else:
                novas_linhas.append([data, medica, f_gasto, f_leads_meta, "", ""])

    ordem_medicas = list(MEDICAS.keys())

    for data in sorted(medicas_por_data.keys()):
        for medica in ordem_medicas:
            if medica in medicas_por_data[data]:
                processar_linha(data, medica)
        processar_linha(data, LABEL_TOTAL_DIA, eh_total=True)

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
            f"sem médica identificada:"
        )
        for texto in list(nao_identificados)[:10]:
            print(f"  - {texto}")


if __name__ == "__main__":
    main()
