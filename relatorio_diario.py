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

# ============================================================
# JANELA: ONTEM (dinâmica, roda sozinha todo dia)
# ============================================================
ontem = (datetime.now(TZ) - timedelta(days=1)).date()
ts0 = int(TZ.localize(datetime.combine(ontem, time.min)).timestamp())
ts1 = int(TZ.localize(datetime.combine(ontem, time.max)).timestamp())
print(f"Coletando dados de: {ontem}")

# ============================================================
# COLETA
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


eventos = get_eventos()
print(f"{len(eventos)} eventos coletados.")

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
# RESUMO POR PESSOA (o que vai pra planilha)
# ============================================================
if df.empty:
    print("Nenhum evento ontem. Nada a enviar.")
    resumo_rows = []
else:
    foco_df = df[df.usuario.isin(FOCO)]
    mov = foco_df[foco_df.tipo == "lead_status_changed"]

    # ações = total de eventos/interações (qualquer tipo)
    acoes_totais = foco_df.groupby("usuario").size()

    # leads = quantidade de leads ÚNICOS com quem a pessoa interagiu
    leads_df = foco_df[foco_df.entity_type == "lead"]
    leads_unicos = leads_df.groupby("usuario")["entity_id"].nunique()

    agendou = mov[mov.destino_etapa == "Agendamento Marcado"].groupby("usuario").size()
    compareceu = mov[mov.destino_etapa == "Compareceu (Ganho)"].groupby("usuario").size()
    faltou = mov[mov.destino_etapa == "Faltou (No Show)"].groupby("usuario").size()

    resumo_rows = []
    for pessoa in FOCO:
        resumo_rows.append(
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

# ============================================================
# ESCREVE NO GOOGLE SHEETS (acumula histórico)
# ============================================================
if resumo_rows:
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    import json

    creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS), scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.sheet1

    cabecalho = ["data", "pessoa", "leads", "ações", "agendamentos", "compareceu", "faltou"]

    # Checagem robusta: olha direto a célula A1 em vez de get_all_values(),
    # que às vezes retorna algo "não-vazio" mesmo numa planilha em branco.
    primeira_celula = ws.acell("A1").value
    if not primeira_celula:
        ws.insert_row(cabecalho, index=1)

    ws.append_rows(resumo_rows, value_input_option="USER_ENTERED")
    print(f"{len(resumo_rows)} linhas enviadas para a planilha.")
else:
    print("Nada enviado.")
