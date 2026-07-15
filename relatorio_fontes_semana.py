"""
Relatório: volume de trabalho por FONTE de lead (WA-Clínica, WA-Dra Bruna,
WA-Isadora, API, etc), da segunda-feira desta semana até agora.

Objetivo: dar evidência concreta pra investigar se o WhatsApp da Dra Bruna
está gerando volume de mensagens desproporcional ao número de leads que traz
— ou seja, se cada lead desse canal está dando muito mais trabalho de
atendimento que o normal.

Variáveis de ambiente esperadas (as mesmas já cadastradas nos Secrets do
repositório, reaproveitadas do relatorio_diario.py):
    KOMMO_SUBDOMAIN, KOMMO_TOKEN, SHEET_ID, GOOGLE_CREDENTIALS

Roda uma vez (não é recorrente) e escreve numa aba nova "Fontes de Lead -
Semana" na mesma planilha, sobrescrevendo o conteúdo a cada execução.
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
FOCO = ["Rayana", "Isadora", "Ana Lívia", "Isabella Eleutério"]

ABA_SAIDA = "Fontes de Lead - Semana"

# ============================================================
# JANELA DE TEMPO: segunda-feira desta semana até agora
# ============================================================
agora = datetime.now(TZ)
hoje = agora.date()
inicio_semana = hoje - timedelta(days=hoje.weekday())  # segunda-feira
ts_inicio_semana = int(TZ.localize(datetime.combine(inicio_semana, datetime.min.time())).timestamp())
ts_inicio_hoje = int(TZ.localize(datetime.combine(hoje, datetime.min.time())).timestamp())
ts_agora = int(agora.timestamp())

print(f"Coletando de {inicio_semana} (segunda-feira) até agora ({agora.strftime('%d/%m %H:%M')})")


# ============================================================
# COLETA: fontes, usuários, eventos, leads
# ============================================================
def get_fontes():
    fontes, page = {}, 1
    while True:
        rr = requests.get(f"{BASE_URL}/sources", headers=HEADERS, params={"page": page, "limit": 250})
        if rr.status_code == 204:
            break
        rr.raise_for_status()
        d = rr.json().get("_embedded", {}).get("sources", [])
        if not d:
            break
        for s in d:
            fontes[s["id"]] = s.get("name", f"Fonte {s['id']}")
        page += 1
    return fontes


def get_usuarios():
    u, page = {}, 1
    while True:
        rr = requests.get(f"{BASE_URL}/users", headers=HEADERS, params={"page": page, "limit": 250})
        if rr.status_code == 204:
            break
        rr.raise_for_status()
        d = rr.json().get("_embedded", {}).get("users", [])
        if not d:
            break
        for x in d:
            u[x["id"]] = x.get("name", f"User {x['id']}")
        page += 1
    return u


def get_eventos(ts0, ts1):
    ev, page = [], 1
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
        ev.extend(d)
        page += 1
        if page > 3000:
            break
    return ev


def get_leads_criados(ts0, ts1):
    leads, page = [], 1
    while True:
        params = {
            "filter[created_at][from]": ts0,
            "filter[created_at][to]": ts1,
            "with": "source_id",
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
        if page > 500:
            break
    return leads


def chunk(lista, tamanho):
    lista = list(lista)
    for i in range(0, len(lista), tamanho):
        yield lista[i:i + tamanho]


def get_leads_por_id(lead_ids):
    leads_map = {}
    for grupo in chunk(sorted(set(lid for lid in lead_ids if lid)), 200):
        page = 1
        while True:
            query = [("with", "source_id"), ("limit", 250), ("page", page)]
            query += [("filter[id][]", lid) for lid in grupo]
            rr = requests.get(f"{BASE_URL}/leads", headers=HEADERS, params=query)
            if rr.status_code == 204:
                break
            rr.raise_for_status()
            d = rr.json().get("_embedded", {}).get("leads", [])
            if not d:
                break
            for lead in d:
                leads_map[lead["id"]] = lead
            if len(d) < 250:
                break
            page += 1
    return leads_map


FONTES = get_fontes()
usuarios = get_usuarios()
print(f"{len(FONTES)} fontes cadastradas no Kommo: {list(FONTES.values())}")

# ============================================================
# ISOLA só as 7 etapas do funil "01: Entrada + Agendamento" — o resto
# do relatório (mensagens, leads novos) passa a contar só quem está
# (ou estava, no momento da coleta) numa dessas etapas.
# ============================================================
PIPELINE_ID_ISOLAR = 11664299  # "01: Entrada + Agendamento"
ETAPAS_ISOLAR = [
    "Entrada de Lead",
    "Aguardando Resposta HJ",
    "Retorno Automação",
    "Investigação",
    "Encontrando Horário",
    "Aguardando Dados",
    "Dúvida Médica",
]


def get_status_do_pipeline(pipeline_id):
    status_por_nome = {}
    rr = requests.get(f"{BASE_URL}/leads/pipelines", headers=HEADERS)
    rr.raise_for_status()
    for p in rr.json().get("_embedded", {}).get("pipelines", []):
        if p["id"] != pipeline_id:
            continue
        for s in p.get("_embedded", {}).get("statuses", []):
            status_por_nome[s["name"]] = s["id"]
    return status_por_nome


status_por_nome = get_status_do_pipeline(PIPELINE_ID_ISOLAR)
etapas_ids_isolar = {status_por_nome[n] for n in ETAPAS_ISOLAR if n in status_por_nome}
if len(etapas_ids_isolar) < len(ETAPAS_ISOLAR):
    faltando = [n for n in ETAPAS_ISOLAR if n not in status_por_nome]
    print(f"AVISO: etapas não encontradas nesse funil: {faltando}")
print(f"Isolando {len(etapas_ids_isolar)}/7 etapas do funil '01: Entrada + Agendamento'.")


def lead_esta_nas_etapas_isoladas(lead):
    return lead.get("pipeline_id") == PIPELINE_ID_ISOLAR and lead.get("status_id") in etapas_ids_isolar


eventos_semana = get_eventos(ts_inicio_semana, ts_agora)
print(f"{len(eventos_semana)} eventos coletados desde segunda-feira.")

eventos_mensagem_todos = [
    e for e in eventos_semana
    if e.get("type") in ("incoming_chat_message", "outgoing_chat_message")
    and e.get("entity_type") == "lead"
]

lead_ids_mensagens = {e["entity_id"] for e in eventos_mensagem_todos if e.get("entity_id")}
leads_map_todos = get_leads_por_id(lead_ids_mensagens)

# filtra: só leads que estão nas 7 etapas isoladas
leads_map = {lid: lead for lid, lead in leads_map_todos.items() if lead_esta_nas_etapas_isoladas(lead)}
eventos_mensagem = [e for e in eventos_mensagem_todos if e.get("entity_id") in leads_map]
print(f"{len(leads_map)} de {len(leads_map_todos)} leads com mensagem essa semana estão nas 7 etapas isoladas.")

leads_criados_semana_todos = get_leads_criados(ts_inicio_semana, ts_agora)
leads_criados_semana = [l for l in leads_criados_semana_todos if lead_esta_nas_etapas_isoladas(l)]
print(f"{len(leads_criados_semana)} de {len(leads_criados_semana_todos)} leads novos essa semana estão nas 7 etapas isoladas.")



# O endpoint /sources dessa conta vem vazio (o WhatsApp Lite não registra
# fonte formal ali, mesmo preenchendo source_id no lead) — mapeamento manual
# confirmado direto no card do lead no Kommo:
FONTES_MANUAIS = {
    78752: "WA - Clínica",
    85238: "WA - Dra Brunna",
    78856: "WA - Isadora",
}


def nome_fonte(source_id):
    if not source_id:
        return "Sem fonte identificada"
    if source_id in FONTES_MANUAIS:
        return FONTES_MANUAIS[source_id]
    return FONTES.get(source_id, f"Fonte desconhecida ({source_id})")


def fonte_do_lead(lead_id):
    lead = leads_map.get(lead_id)
    if not lead:
        return "Lead não encontrado"
    return nome_fonte(lead.get("source_id"))


# ============================================================
# AGREGAÇÕES
# ============================================================
msgs_por_fonte_semana = defaultdict(int)
msgs_por_fonte_hoje = defaultdict(int)
msgs_por_fonte_pessoa = defaultdict(lambda: defaultdict(int))
leads_ativos_por_fonte_semana = defaultdict(set)

for e in eventos_mensagem:
    lead_id = e.get("entity_id")
    fonte = fonte_do_lead(lead_id)
    msgs_por_fonte_semana[fonte] += 1
    leads_ativos_por_fonte_semana[fonte].add(lead_id)
    if e["created_at"] >= ts_inicio_hoje:
        msgs_por_fonte_hoje[fonte] += 1
    if e.get("type") == "outgoing_chat_message":
        uid = e.get("created_by", 0)
        nome = usuarios.get(uid, f"User {uid}")
        if nome in FOCO:
            msgs_por_fonte_pessoa[fonte][nome] += 1

leads_novos_por_fonte_semana = defaultdict(int)
leads_novos_por_fonte_hoje = defaultdict(int)
for lead in leads_criados_semana:
    fonte = nome_fonte(lead.get("source_id"))
    leads_novos_por_fonte_semana[fonte] += 1
    if lead["created_at"] >= ts_inicio_hoje:
        leads_novos_por_fonte_hoje[fonte] += 1

todas_fontes = sorted(
    set(msgs_por_fonte_semana) | set(leads_novos_por_fonte_semana),
    key=lambda f: -msgs_por_fonte_semana.get(f, 0)
)

print("\n=== RESUMO POR FONTE (essa semana) ===")
for fonte in todas_fontes:
    leads_semana = leads_novos_por_fonte_semana.get(fonte, 0)
    msgs_semana = msgs_por_fonte_semana.get(fonte, 0)
    leads_ativos = len(leads_ativos_por_fonte_semana.get(fonte, set()))
    media = round(msgs_semana / leads_ativos, 1) if leads_ativos else 0
    print(f"{fonte}: {leads_semana} leads novos | {msgs_semana} mensagens | média {media} msgs/lead ativo")


# ============================================================
# ESCREVE NO GOOGLE SHEETS (aba nova, sobrescrita a cada rodada)
# ============================================================
scopes = ["https://www.googleapis.com/auth/spreadsheets"]
creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS), scopes=scopes)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SHEET_ID)

try:
    ws = sh.worksheet(ABA_SAIDA)
    ws.clear()
except gspread.exceptions.WorksheetNotFound:
    ws = sh.add_worksheet(title=ABA_SAIDA, rows=200, cols=10)

linhas = []
linhas.append([f"Gerado em {agora.strftime('%d/%m/%Y %H:%M')} — dados de {inicio_semana.strftime('%d/%m/%Y')} até agora"])
linhas.append([f"Isolado só nas 7 etapas de '01: Entrada + Agendamento': {', '.join(ETAPAS_ISOLAR)}"])
linhas.append([])
linhas.append(["RESUMO POR FONTE"])
linhas.append(["Fonte", "Leads Novos (Semana)", "Leads Novos (Hoje)", "Mensagens (Semana)",
                "Mensagens (Hoje)", "Leads c/ mensagem (Semana)", "Média Msgs/Lead (Semana)"])
for fonte in todas_fontes:
    leads_semana = leads_novos_por_fonte_semana.get(fonte, 0)
    leads_hoje = leads_novos_por_fonte_hoje.get(fonte, 0)
    msgs_semana = msgs_por_fonte_semana.get(fonte, 0)
    msgs_hoje = msgs_por_fonte_hoje.get(fonte, 0)
    leads_ativos = len(leads_ativos_por_fonte_semana.get(fonte, set()))
    media = round(msgs_semana / leads_ativos, 1) if leads_ativos else 0
    linhas.append([fonte, leads_semana, leads_hoje, msgs_semana, msgs_hoje, leads_ativos, media])

linhas.append([])
linhas.append(["MENSAGENS POR FONTE x PESSOA (semana, só mensagens enviadas pela equipe)"])
linhas.append(["Fonte"] + FOCO)
for fonte in todas_fontes:
    linha = [fonte] + [msgs_por_fonte_pessoa[fonte].get(p, 0) for p in FOCO]
    linhas.append(linha)

ws.update(range_name="A1", values=linhas, value_input_option="USER_ENTERED")
print(f"\nRelatório gravado na aba '{ABA_SAIDA}'.")
