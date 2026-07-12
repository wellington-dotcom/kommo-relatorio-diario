import os
import json
import calendar
from collections import Counter

import requests
import pandas as pd
import pytz
from datetime import datetime, timedelta, date, time

import gspread
from google.oauth2.service_account import Credentials

# ============================================================
# CONFIG (vem das "Secrets" do GitHub, NUNCA escrito aqui direto)
# ============================================================
SUBDOMINIO = "isapaulaeleuterio"
TOKEN = os.environ["KOMMO_TOKEN"]
SHEET_ID = os.environ["SHEET_ID"]
GOOGLE_CREDS = os.environ["GOOGLE_CREDENTIALS"]

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
# JANELA: mês escolhido no "Run workflow" (ANO / MES), com fallback
# pro mês atual se rodar direto sem preencher nada
# ============================================================
hoje = datetime.now(TZ).date()
ano = int(os.environ.get("ANO", hoje.year))
mes = int(os.environ.get("MES", hoje.month))

data_inicio = date(ano, mes, 1)
ultimo_dia_calendario = calendar.monthrange(ano, mes)[1]
data_fim_calendario = date(ano, mes, ultimo_dia_calendario)

if ano == hoje.year and mes == hoje.month:
    # mês corrente: não processa hoje (dia ainda não fechou) nem dias futuros
    data_fim = min(data_fim_calendario, hoje - timedelta(days=1))
else:
    data_fim = data_fim_calendario

if data_fim < data_inicio:
    print(f"Nada a processar: {ano}-{mes:02d} ainda não teve nenhum dia fechado.")
    raise SystemExit(0)

ts0 = int(TZ.localize(datetime.combine(data_inicio, time.min)).timestamp())
ts1 = int(TZ.localize(datetime.combine(data_fim, time.max)).timestamp())
print(f"Backfill de {data_inicio} até {data_fim} (mês {mes:02d}/{ano})")

# ============================================================
# COLETA: pipeline / status / usuários
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


def get_eventos(ts_from, ts_to):
    ev, page = [], 1
    while True:
        params = {
            "filter[created_at][from]": ts_from,
            "filter[created_at][to]": ts_to,
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
        if page > 5000:
            break
    return ev


def get_leads_novos(ts_from, ts_to):
    leads, page = [], 1
    while True:
        params = {
            "filter[created_at][from]": ts_from,
            "filter[created_at][to]": ts_to,
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
        if page > 1000:
            break
    return leads


print("Coletando eventos do período inteiro (uma única passada)...")
eventos = get_eventos(ts0, ts1)
print(f"{len(eventos)} eventos coletados no total.")

print("Coletando leads novos do período inteiro...")
leads_novos = get_leads_novos(ts0, ts1)
print(f"{len(leads_novos)} leads novos no total.")

# --- monta dataframe de eventos, igual ao script diário ---
linhas = []
for e in eventos:
    uid = e.get("created_by", 0)
    nome = usuarios.get(uid, f"User {uid}")
    tipo = e.get("type", "?")
    entity_type = e.get("entity_type", "?")
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
        }
    )

df = pd.DataFrame(linhas)
if not df.empty:
    df = df[df["usuario"] != "User 0"]

# --- contador de leads novos por dia ---
leads_novos_por_dia = Counter()
for lead in leads_novos:
    dia = datetime.fromtimestamp(lead["created_at"], TZ).date()
    leads_novos_por_dia[dia] += 1

# ============================================================
# LOOP DIA A DIA: monta as mesmas linhas que o script diário monta
# ============================================================
resumo_pessoa_rows = []
resumo_bruto_rows = []
mix_rows = []
movimentacao_rows = []
leads_novos_rows = []

dia_atual = data_inicio
while dia_atual <= data_fim:
    dia_df = df[df["data"] == dia_atual] if not df.empty else df

    if not dia_df.empty:
        foco_df = dia_df[dia_df.usuario.isin(FOCO)]
        mov = foco_df[foco_df.tipo == "lead_status_changed"]

        acoes_totais = foco_df.groupby("usuario").size()
        leads_df = foco_df[foco_df.entity_type == "lead"]
        leads_unicos = leads_df.groupby("usuario")["entity_id"].nunique()

        agendou = mov[mov.destino_etapa == "Agendamento Marcado"].groupby("usuario").size()
        compareceu = mov[mov.destino_etapa == "Compareceu (Ganho)"].groupby("usuario").size()
        faltou = mov[mov.destino_etapa == "Faltou (No Show)"].groupby("usuario").size()

        for pessoa in FOCO:
            resumo_pessoa_rows.append(
                [
                    str(dia_atual),
                    pessoa,
                    int(leads_unicos.get(pessoa, 0)),
                    int(acoes_totais.get(pessoa, 0)),
                    int(agendou.get(pessoa, 0)),
                    int(compareceu.get(pessoa, 0)),
                    int(faltou.get(pessoa, 0)),
                ]
            )

        resumo_bruto_rows.append(
            [
                str(dia_atual),
                int(leads_unicos.sum()),
                int(acoes_totais.sum()),
                int(agendou.sum()),
                int(compareceu.sum()),
                int(faltou.sum()),
            ]
        )

        if not foco_df.empty:
            mix_counts = foco_df.groupby(["usuario", "tipo"]).size()
            for (pessoa, tipo), qtd in mix_counts.items():
                mix_rows.append([str(dia_atual), pessoa, tipo, int(qtd)])

        if not mov.empty:
            mov_counts = mov.groupby(["usuario", "destino_etapa"]).size()
            for (pessoa, etapa), qtd in mov_counts.items():
                movimentacao_rows.append([str(dia_atual), pessoa, etapa, int(qtd)])
    else:
        # dia sem nenhum evento: ainda registra zero no bruto, pra não deixar buraco na série
        resumo_bruto_rows.append([str(dia_atual), 0, 0, 0, 0, 0])

    leads_novos_rows.append([str(dia_atual), leads_novos_por_dia.get(dia_atual, 0)])

    dia_atual += timedelta(days=1)

print(f"Dias processados: {(data_fim - data_inicio).days + 1}")
print(f"Linhas 'Relatório por Pessoa': {len(resumo_pessoa_rows)}")
print(f"Linhas 'Números Brutos Diários': {len(resumo_bruto_rows)}")
print(f"Linhas 'Mix de Tipos': {len(mix_rows)}")
print(f"Linhas 'Movimentações': {len(movimentacao_rows)}")
print(f"Linhas 'Leads Novos': {len(leads_novos_rows)}")

# ============================================================
# ESCREVE NO GOOGLE SHEETS (mesmas 5 abas do script diário)
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
if resumo_bruto_rows:
    ws_bruto.append_rows(resumo_bruto_rows, value_input_option="USER_ENTERED")
if mix_rows:
    ws_mix.append_rows(mix_rows, value_input_option="USER_ENTERED")
if movimentacao_rows:
    ws_movimentacao.append_rows(movimentacao_rows, value_input_option="USER_ENTERED")
if leads_novos_rows:
    ws_leads_novos.append_rows(leads_novos_rows, value_input_option="USER_ENTERED")

print(f"Backfill de {mes:02d}/{ano} concluído — confira as abas na planilha.")
