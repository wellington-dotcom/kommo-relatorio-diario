"""
Relatório: gargalos no funil "01: Entrada + Agendamento", isolando 7 etapas
específicas: Entrada de Lead, Aguardando Resposta HJ, Retorno Automação,
Investigação, Encontrando Horário, Aguardando Dados, Dúvida Médica.

Duas visões:
1) FOTO DE AGORA — quantos leads estão parados em cada etapa neste momento,
   e há quantos dias cada um está lá sem se mover (usando o filtro nativo
   do Kommo de "mudança de etapa por pipeline+status", pra não precisar
   baixar o histórico inteiro da conta).
2) MOVIMENTO DA SEMANA — quantos leads entraram e quantos saíram de cada
   etapa desde segunda-feira até agora.

Variáveis de ambiente: as mesmas já cadastradas (KOMMO_SUBDOMAIN, KOMMO_TOKEN,
SHEET_ID, GOOGLE_CREDENTIALS).
"""

import os
import json
from datetime import datetime, timedelta
from collections import defaultdict

import requests
import pytz
import gspread
from google.oauth2.service_account import Credentials

SUBDOMINIO = os.environ["KOMMO_SUBDOMAIN"]
TOKEN = os.environ["KOMMO_TOKEN"]
SHEET_ID = os.environ["SHEET_ID"]
GOOGLE_CREDS = os.environ["GOOGLE_CREDENTIALS"]

BASE_URL = f"https://{SUBDOMINIO}.kommo.com/api/v4"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
TZ = pytz.timezone("America/Sao_Paulo")

PIPELINE_ID = 11664299  # "01: Entrada + Agendamento"
ETAPAS_DESEJADAS = [
    "Entrada de Lead",
    "Aguardando Resposta HJ",
    "Retorno Automação",
    "Investigação",
    "Encontrando Horário",
    "Aguardando Dados",
    "Dúvida Médica",
]
DIAS_LOOKBACK_HISTORICO = 90  # janela pra achar a última entrada de cada lead na etapa

ABA_SAIDA = "Gargalos - Entrada e Agendamento"

agora = datetime.now(TZ)
hoje = agora.date()
inicio_semana = hoje - timedelta(days=hoje.weekday())
ts_inicio_semana = int(TZ.localize(datetime.combine(inicio_semana, datetime.min.time())).timestamp())
ts_lookback = int(TZ.localize(datetime.combine(hoje - timedelta(days=DIAS_LOOKBACK_HISTORICO), datetime.min.time())).timestamp())
ts_agora = int(agora.timestamp())

print(f"Funil '01: Entrada + Agendamento' — foto de agora + movimento desde {inicio_semana} (segunda-feira)")


# ============================================================
# NOMES DE ETAPA -> ID, só do funil que interessa
# ============================================================
def get_status_do_pipeline(pipeline_id):
    status_por_nome, status_por_id = {}, {}
    rr = requests.get(f"{BASE_URL}/leads/pipelines", headers=HEADERS)
    rr.raise_for_status()
    for p in rr.json().get("_embedded", {}).get("pipelines", []):
        if p["id"] != pipeline_id:
            continue
        for s in p.get("_embedded", {}).get("statuses", []):
            status_por_nome[s["name"]] = s["id"]
            status_por_id[s["id"]] = s["name"]
    return status_por_nome, status_por_id


status_por_nome, status_por_id = get_status_do_pipeline(PIPELINE_ID)

etapas_ids = {}
for nome in ETAPAS_DESEJADAS:
    if nome in status_por_nome:
        etapas_ids[status_por_nome[nome]] = nome
    else:
        print(f"AVISO: etapa '{nome}' não encontrada nesse funil — conferir o nome exato no Kommo.")

if not etapas_ids:
    raise SystemExit("Nenhuma das 7 etapas foi encontrada — conferir PIPELINE_ID e os nomes.")

print(f"Etapas identificadas ({len(etapas_ids)}/7): {list(etapas_ids.values())}")


# ============================================================
# LEADS ATUAIS NO FUNIL (foto de agora)
# ============================================================
def get_leads_pipeline(pipeline_id):
    leads, page = [], 1
    while True:
        params = {
            "filter[pipeline_id]": pipeline_id,
            "page": page,
            "limit": 250,
        }
        rr = requests.get(f"{BASE_URL}/leads", headers=HEADERS, params=params)
        if rr.status_code == 204:
            break
        rr.raise_for_status()
        d = rr.json().get("_embedded", {}).get("leads", [])
        if not d:
            break
        leads.extend(d)
        page += 1
        if page > 1000:
            break
    return leads


leads_pipeline = get_leads_pipeline(PIPELINE_ID)
print(f"{len(leads_pipeline)} leads no total nesse funil (todas as etapas, incluindo ganhos/perdidos).")

leads_nas_etapas = [l for l in leads_pipeline if l.get("status_id") in etapas_ids]
print(f"{len(leads_nas_etapas)} leads parados agora numa das 7 etapas de interesse.")


# ============================================================
# ÚLTIMA ENTRADA DE CADA LEAD EM CADA ETAPA (pra saber há quanto tempo
# está parado) — usa o filtro nativo do Kommo por pipeline+status, então
# só baixa os eventos relevantes pra essas 7 etapas, não a conta inteira.
# ============================================================
def get_eventos_entrada_na_etapa(pipeline_id, status_id, ts0, ts1):
    eventos, page = [], 1
    while True:
        params = {
            "filter[value_after][leads_statuses][0][pipeline_id]": pipeline_id,
            "filter[value_after][leads_statuses][0][status_id]": status_id,
            "filter[created_at][from]": ts0,
            "filter[created_at][to]": ts1,
            "page": page,
            "limit": 250,
        }
        rr = requests.get(f"{BASE_URL}/events", headers=HEADERS, params=params)
        if rr.status_code == 204:
            break
        rr.raise_for_status()
        d = rr.json().get("_embedded", {}).get("events", [])
        if not d:
            break
        eventos.extend(d)
        page += 1
        if page > 1000:
            break
    return eventos


ultima_entrada_por_lead_e_etapa = {}  # (lead_id, status_id) -> timestamp mais recente
for status_id in etapas_ids:
    eventos_etapa = get_eventos_entrada_na_etapa(PIPELINE_ID, status_id, ts_lookback, ts_agora)
    print(f"  '{etapas_ids[status_id]}': {len(eventos_etapa)} entradas nos últimos {DIAS_LOOKBACK_HISTORICO} dias.")
    for e in eventos_etapa:
        if e.get("entity_type") != "lead":
            continue
        lead_id = e.get("entity_id")
        ts = e["created_at"]
        chave = (lead_id, status_id)
        if chave not in ultima_entrada_por_lead_e_etapa or ts > ultima_entrada_por_lead_e_etapa[chave]:
            ultima_entrada_por_lead_e_etapa[chave] = ts

leads_por_etapa_agora = defaultdict(list)
for lead in leads_nas_etapas:
    lead_id = lead["id"]
    status_id = lead.get("status_id")
    ts_entrada = ultima_entrada_por_lead_e_etapa.get((lead_id, status_id), lead.get("created_at"))
    dias_parado = round((ts_agora - ts_entrada) / 86400, 1)
    leads_por_etapa_agora[status_id].append((lead_id, lead.get("name", f"Lead {lead_id}"), dias_parado))


# ============================================================
# MOVIMENTO DA SEMANA — entradas e saídas de cada etapa (segunda até agora)
# ============================================================
entradas_semana_por_etapa = defaultdict(int)
for status_id in etapas_ids:
    eventos_semana_etapa = get_eventos_entrada_na_etapa(PIPELINE_ID, status_id, ts_inicio_semana, ts_agora)
    entradas_semana_por_etapa[status_id] = len([e for e in eventos_semana_etapa if e.get("entity_type") == "lead"])

# saídas: eventos de mudança de etapa nesta semana, cujo status ANTERIOR
# (value_before) era uma das 7 etapas — aqui sim precisa olhar todos os
# eventos de status change da semana (volume já validado como tratável).
def get_eventos_semana_todos_status_changed(ts0, ts1):
    eventos, page = [], 1
    while True:
        params = {
            "filter[created_at][from]": ts0,
            "filter[created_at][to]": ts1,
            "page": page,
            "limit": 250,
        }
        rr = requests.get(f"{BASE_URL}/events", headers=HEADERS, params=params)
        if rr.status_code == 204:
            break
        rr.raise_for_status()
        d = rr.json().get("_embedded", {}).get("events", [])
        if not d:
            break
        eventos.extend(d)
        page += 1
        if page > 3000:
            break
    return eventos


eventos_semana = get_eventos_semana_todos_status_changed(ts_inicio_semana, ts_agora)
eventos_status_changed_semana = [
    e for e in eventos_semana
    if e.get("type") == "lead_status_changed" and e.get("entity_type") == "lead"
]
print(f"{len(eventos_status_changed_semana)} mudanças de etapa (todo o Kommo) desde segunda-feira.")

saidas_semana_por_etapa = defaultdict(int)
for e in eventos_status_changed_semana:
    try:
        status_antes = e["value_before"][0]["lead_status"]["id"]
    except (KeyError, IndexError, TypeError):
        continue
    if status_antes in etapas_ids:
        saidas_semana_por_etapa[status_antes] += 1


# ============================================================
# ESCREVE NO GOOGLE SHEETS
# ============================================================
scopes = ["https://www.googleapis.com/auth/spreadsheets"]
creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS), scopes=scopes)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SHEET_ID)

try:
    ws = sh.worksheet(ABA_SAIDA)
    ws.clear()
except gspread.exceptions.WorksheetNotFound:
    ws = sh.add_worksheet(title=ABA_SAIDA, rows=1000, cols=10)

ordem_etapas = [nome for nome in ETAPAS_DESEJADAS if nome in status_por_nome]

linhas = []
linhas.append([f"Gerado em {agora.strftime('%d/%m/%Y %H:%M')} — funil '01: Entrada + Agendamento'"])
linhas.append([])
linhas.append(["RESUMO POR ETAPA"])
linhas.append(["Etapa", "Leads parados agora", "Média de dias parado", "Entraram (semana)", "Saíram (semana)"])
for nome in ordem_etapas:
    sid = status_por_nome[nome]
    parados = leads_por_etapa_agora.get(sid, [])
    media_dias = round(sum(d for _, _, d in parados) / len(parados), 1) if parados else 0
    linhas.append([
        nome,
        len(parados),
        media_dias,
        entradas_semana_por_etapa.get(sid, 0),
        saidas_semana_por_etapa.get(sid, 0),
    ])

linhas.append([])
linhas.append(["DETALHE — LEADS PARADOS AGORA, POR ETAPA (do mais parado pro mais recente)"])
linhas.append(["Etapa", "Lead", "Dias parado nessa etapa"])
for nome in ordem_etapas:
    sid = status_por_nome[nome]
    parados = sorted(leads_por_etapa_agora.get(sid, []), key=lambda x: -x[2])
    for lead_id, lead_nome, dias in parados:
        linhas.append([nome, lead_nome, dias])

ws.update(range_name="A1", values=linhas, value_input_option="USER_ENTERED")
print(f"\nRelatório gravado na aba '{ABA_SAIDA}'.")
