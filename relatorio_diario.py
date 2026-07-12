import os
import requests
import pandas as pd
import pytz
from datetime import datetime, timedelta, time

import gspread
from google.oauth2.service_account import Credentials

# ============================================================
# CONFIG (vem das "Secrets" do GitHub, NUNCA escrito aqui direto)
# ============================================================
SUBDOMINIO = "isapaulaeleuterio"
TOKEN = os.environ["KOMMO_TOKEN"]                 # token da Kommo
SHEET_ID = os.environ["SHEET_ID"]                 # ID da planilha do Google
GOOGLE_CREDS = os.environ["GOOGLE_CREDENTIALS"]   # JSON da service account

BASE_URL = f"https://{SUBDOMINIO}.kommo.com/api/v4"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
TZ = pytz.timezone("America/Sao_Paulo")
PIPELINE_ID = 11664299
FOCO = ["Rayana", "Isadora", "Ana Lívia", "Isabella Eleutério"]

ABA_PESSOA = "Relatório por Pessoa"
ABA_BRUTO = "Números Brutos Diários"
ABA_MIX = "Mix de Tipos por Pessoa"
ABA_MOVIMENTACAO = "Movimentações por Etapa"
ABA_LEADS_NOVOS = "Leads Novos por Dia"

# ============================================================
# JANELA: ONTEM (dinâmica, roda sozinha todo dia)
# ============================================================
ontem = (datetime.now(TZ) - timedelta(days=1)).date()
ts0 = int(TZ.localize(datetime.combine(ontem, time.min)).timestamp())
ts1 = int(TZ.localize(datetime.combine(ontem, time.max)).timestamp())
print(f"Coletando dados de: {ontem}")

# ============================================================
# COLETA: pipeline / status
# ============================================================
r = requests.get(f"{BASE_URL}/leads/pipelines/{PIPELINE_ID}", headers=HEADERS)
r.raise_for_status()
STATUS = {s["id"]: s["name"] for s in r.json().get("_embedded", {}).get("statuses", [])}


def get_usuarios():
    u, page = {}, 1
    while True:
        rr = requests.get(
            f"{BASE_URL}/users",
            headers=HEADERS,
            params={"page": page, "limit": 250},
        )
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


usuarios = get_usuarios()


def get_eventos():
    ev, page = [], 1
    while True:
        params = {
            "filter[created_at][from]": ts0,
            "filter[created_at][to]": ts1,
            "page": page,
            "limit": 100,
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
        if page > 2000:
            break
    return ev


def get_leads_novos():
    """Leads criados no pipeline dentro da janela de 'ontem' (script 2)."""
    leads, page = [], 1
    while True:
        params = {
            "filter[created_at][from]": ts0,
            "filter[created_at][to]": ts1,
            "filter[pipeline_id]": PIPELINE_ID,
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


eventos = get_eventos()
print(f"{len(eventos)} eventos coletados.")

leads_novos = get_leads_novos()
print(f"{len(leads_novos)} leads novos criados ontem.")

linhas = []
for e in eventos:
    uid = e.get("created_by", 0)
    nome = usuarios.get(uid, f"User {uid}")
    tipo = e.get("type", "?")
    entity_type = e.get("entity_type", "?")   # ex: "lead", "contact", "task"...
    entity_id = e.get("entity_id")
    destino = None
    if tipo == "lead_status_changed":
        try:
            destino = STATUS.get(e["value_after"][0]["lead_status"]["id"], "?")
        except (IndexError, KeyError, TypeError):
            destino = "?"
    dt = datetime.fromtimestamp(e["created_at"], TZ)
    linhas.append(
        {
            "usuario": nome,
            "tipo": tipo,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "destino_etapa": destino,
            "data": dt.date(),
            "hora": dt.hour,
        }
    )

df = pd.DataFrame(linhas)
if not df.empty:
    df = df[df["usuario"] != "User 0"]

# ============================================================
# RESUMOS
# ============================================================
resumo_pessoa_rows = []
resumo_bruto_row = None
mix_rows = []
movimentacao_rows = []

if df.empty:
    print("Nenhum evento ontem.")
else:
    foco_df = df[df.usuario.isin(FOCO)]
    mov = foco_df[foco_df.tipo == "lead_status_changed"]

    # ---- Relatório por Pessoa ----
    acoes_totais = foco_df.groupby("usuario").size()

    leads_df = foco_df[foco_df.entity_type == "lead"]
    leads_unicos = leads_df.groupby("usuario")["entity_id"].nunique()

    agendou = mov[mov.destino_etapa == "Agendamento Marcado"].groupby("usuario").size()
    compareceu = mov[mov.destino_etapa == "Compareceu (Ganho)"].groupby("usuario").size()
    faltou = mov[mov.destino_etapa == "Faltou (No Show)"].groupby("usuario").size()

    for pessoa in FOCO:
        resumo_pessoa_rows.append(
            [
                str(ontem),
                pessoa,
                int(leads_unicos.get(pessoa, 0)),
                int(acoes_totais.get(pessoa, 0)),
                int(agendou.get(pessoa, 0)),
                int(compareceu.get(pessoa, 0)),
                int(faltou.get(pessoa, 0)),
            ]
        )

    # ---- Números Brutos Diários (soma do grupo inteiro) ----
    resumo_bruto_row = [
        str(ontem),
        int(leads_unicos.sum()),
        int(acoes_totais.sum()),
        int(agendou.sum()),
        int(compareceu.sum()),
        int(faltou.sum()),
    ]

    # ---- Mix de Tipos por Pessoa ----
    if not foco_df.empty:
        mix_counts = foco_df.groupby(["usuario", "tipo"]).size()
        for (pessoa, tipo), qtd in mix_counts.items():
            mix_rows.append([str(ontem), pessoa, tipo, int(qtd)])

    # ---- Movimentações por Etapa (todas as etapas, não só as 3 principais) ----
    if not mov.empty:
        mov_counts = mov.groupby(["usuario", "destino_etapa"]).size()
        for (pessoa, etapa), qtd in mov_counts.items():
            movimentacao_rows.append([str(ontem), pessoa, etapa, int(qtd)])

# ---- Leads Novos por Dia ----
leads_novos_row = [str(ontem), len(leads_novos)]

# ============================================================
# ESCREVE NO GOOGLE SHEETS (cinco abas)
# ============================================================


def get_or_create_ws(sh, titulo, cabecalho):
    try:
        ws = sh.worksheet(titulo)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=titulo, rows=1000, cols=max(10, len(cabecalho)))

    primeira_celula = ws.acell("A1").value
    if not primeira_celula:
        ws.insert_row(cabecalho, index=1)
    return ws


scopes = ["https://www.googleapis.com/auth/spreadsheets"]
import json

creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS), scopes=scopes)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SHEET_ID)

cabecalho_pessoa = ["data", "pessoa", "leads", "ações", "agendamentos", "compareceu", "faltou"]
cabecalho_bruto = ["data", "leads", "ações", "agendamentos", "compareceu", "faltou"]
cabecalho_mix = ["data", "pessoa", "tipo_evento", "quantidade"]
cabecalho_movimentacao = ["data", "pessoa", "etapa_destino", "quantidade"]
cabecalho_leads_novos = ["data", "leads_novos"]

ws_pessoa = get_or_create_ws(sh, ABA_PESSOA, cabecalho_pessoa)
ws_bruto = get_or_create_ws(sh, ABA_BRUTO, cabecalho_bruto)
ws_mix = get_or_create_ws(sh, ABA_MIX, cabecalho_mix)
ws_movimentacao = get_or_create_ws(sh, ABA_MOVIMENTACAO, cabecalho_movimentacao)
ws_leads_novos = get_or_create_ws(sh, ABA_LEADS_NOVOS, cabecalho_leads_novos)

if resumo_pessoa_rows:
    ws_pessoa.append_rows(resumo_pessoa_rows, value_input_option="USER_ENTERED")
    print(f"{len(resumo_pessoa_rows)} linhas enviadas para '{ABA_PESSOA}'.")

if resumo_bruto_row:
    ws_bruto.append_row(resumo_bruto_row, value_input_option="USER_ENTERED")
    print(f"1 linha enviada para '{ABA_BRUTO}'.")

if mix_rows:
    ws_mix.append_rows(mix_rows, value_input_option="USER_ENTERED")
    print(f"{len(mix_rows)} linhas enviadas para '{ABA_MIX}'.")

if movimentacao_rows:
    ws_movimentacao.append_rows(movimentacao_rows, value_input_option="USER_ENTERED")
    print(f"{len(movimentacao_rows)} linhas enviadas para '{ABA_MOVIMENTACAO}'.")

ws_leads_novos.append_row(leads_novos_row, value_input_option="USER_ENTERED")
print(f"1 linha enviada para '{ABA_LEADS_NOVOS}'.")
