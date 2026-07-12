import os
import json

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
# TRADUÇÃO DE TIPOS DE EVENTO (nomes fixos do sistema Kommo)
# ============================================================
TIPOS_TRADUZIDOS = {
    "lead_added": "Lead criado",
    "lead_deleted": "Lead excluído",
    "lead_status_changed": "Mudança de etapa",
    "name_field_changed": "Nome alterado",
    "entity_responsible_changed": "Responsável alterado",
    "entity_tag_added": "Tag adicionada",
    "entity_tag_deleted": "Tag removida",
    "entity_linked": "Vinculado a outro registro",
    "entity_unlinked": "Desvinculado de registro",
    "entity_merged": "Registros mesclados",
    "entity_direct_message": "Mensagem direta",
    "incoming_chat_message": "Mensagem recebida (chat)",
    "outgoing_chat_message": "Mensagem enviada (chat)",
    "conversation_answered": "Conversa respondida",
    "conversation_assigned": "Conversa atribuída",
    "talk_created": "Conversa iniciada",
    "talk_closed": "Conversa encerrada",
    "talk_deleted": "Conversa excluída",
    "common_note_added": "Nota adicionada",
    "common_note_deleted": "Nota excluída",
    "task_added": "Tarefa criada",
    "task_deleted": "Tarefa excluída",
    "task_completed": "Tarefa concluída",
    "task_text_changed": "Descrição da tarefa alterada",
    "task_type_changed": "Tipo de tarefa alterado",
    "task_deadline_changed": "Prazo da tarefa alterado",
    "sale_field_changed": "Valor do negócio alterado",
    "company_added": "Empresa cadastrada",
    "company_deleted": "Empresa excluída",
    "contact_added": "Contato cadastrado",
    "contact_deleted": "Contato excluído",
}


def nome_tipo(tipo, campos_personalizados):
    if tipo.startswith("custom_field_") and tipo.endswith("_value_changed"):
        meio = tipo[len("custom_field_"):-len("_value_changed")]
        try:
            campo_id = int(meio)
        except ValueError:
            campo_id = None
        nome_campo = campos_personalizados.get(campo_id, f"campo personalizado {meio}")
        return f"Campo alterado: {nome_campo}"
    return TIPOS_TRADUZIDOS.get(tipo, tipo)


# ============================================================
# JANELA: HOJE, da meia-noite até agora (atualiza ao longo do dia)
# ============================================================
agora = datetime.now(TZ)
hoje = agora.date()
ts0 = int(TZ.localize(datetime.combine(hoje, time.min)).timestamp())
ts1 = int(agora.timestamp())
print(f"Coletando dados de hoje ({hoje}) até {agora.strftime('%H:%M')}")

# ============================================================
# COLETA: status de TODOS os pipelines (corrige o bug do "?")
# ============================================================
def get_todos_status():
    status_map = {}
    rr = requests.get(f"{BASE_URL}/leads/pipelines", headers=HEADERS)
    rr.raise_for_status()
    pipelines = rr.json().get("_embedded", {}).get("pipelines", [])
    for p in pipelines:
        for s in p.get("_embedded", {}).get("statuses", []):
            status_map[s["id"]] = s["name"]
    return status_map


STATUS = get_todos_status()


def get_custom_fields():
    campos = {}
    page = 1
    while True:
        rr = requests.get(
            f"{BASE_URL}/leads/custom_fields",
            headers=HEADERS,
            params={"page": page, "limit": 250},
        )
        if rr.status_code == 204:
            break
        rr.raise_for_status()
        d = rr.json().get("_embedded", {}).get("custom_fields", [])
        if not d:
            break
        for cf in d:
            campos[cf["id"]] = cf.get("name", f"Campo {cf['id']}")
        page += 1
        if page > 50:
            break
    return campos


CAMPOS_PERSONALIZADOS = get_custom_fields()


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
print(f"{len(eventos)} eventos coletados hoje até agora.")

leads_novos = get_leads_novos()
print(f"{len(leads_novos)} leads novos hoje até agora.")

linhas = []
for e in eventos:
    uid = e.get("created_by", 0)
    nome = usuarios.get(uid, f"User {uid}")
    tipo_bruto = e.get("type", "?")
    tipo = nome_tipo(tipo_bruto, CAMPOS_PERSONALIZADOS)
    entity_type = e.get("entity_type", "?")
    entity_id = e.get("entity_id")
    destino = None
    if tipo_bruto == "lead_status_changed":
        try:
            status_id = e["value_after"][0]["lead_status"]["id"]
            destino = STATUS.get(status_id, "Etapa não identificada")
        except (IndexError, KeyError, TypeError):
            destino = "Etapa não identificada"
    linhas.append(
        {
            "usuario": nome,
            "tipo_bruto": tipo_bruto,
            "tipo": tipo,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "destino_etapa": destino,
        }
    )

df = pd.DataFrame(linhas)
if not df.empty:
    df = df[df["usuario"] != "User 0"]

# ============================================================
# RESUMOS (sempre o total acumulado de HOJE, do zero até agora)
# ============================================================
resumo_pessoa_rows = []
resumo_bruto_row = [str(hoje), 0, 0, 0, 0, 0]
mix_rows = []
movimentacao_rows = []

if not df.empty:
    foco_df = df[df.usuario.isin(FOCO)]
    mov = foco_df[foco_df.tipo_bruto == "lead_status_changed"]

    acoes_totais = foco_df.groupby("usuario").size()
    leads_df = foco_df[foco_df.entity_type == "lead"]
    leads_unicos = leads_df.groupby("usuario")["entity_id"].nunique()

    agendou = mov[mov.destino_etapa == "Agendamento Marcado"].groupby("usuario").size()
    compareceu = mov[mov.destino_etapa == "Compareceu (Ganho)"].groupby("usuario").size()
    faltou = mov[mov.destino_etapa == "Faltou (No Show)"].groupby("usuario").size()

    for pessoa in FOCO:
        resumo_pessoa_rows.append(
            [
                str(hoje),
                pessoa,
                int(leads_unicos.get(pessoa, 0)),
                int(acoes_totais.get(pessoa, 0)),
                int(agendou.get(pessoa, 0)),
                int(compareceu.get(pessoa, 0)),
                int(faltou.get(pessoa, 0)),
            ]
        )

    resumo_bruto_row = [
        str(hoje),
        int(leads_unicos.sum()),
        int(acoes_totais.sum()),
        int(agendou.sum()),
        int(compareceu.sum()),
        int(faltou.sum()),
    ]

    if not foco_df.empty:
        mix_counts = foco_df.groupby(["usuario", "tipo"]).size()
        for (pessoa, tipo), qtd in mix_counts.items():
            mix_rows.append([str(hoje), pessoa, tipo, int(qtd)])

    if not mov.empty:
        mov_counts = mov.groupby(["usuario", "destino_etapa"]).size()
        for (pessoa, etapa), qtd in mov_counts.items():
            movimentacao_rows.append([str(hoje), pessoa, etapa, int(qtd)])
else:
    for pessoa in FOCO:
        resumo_pessoa_rows.append([str(hoje), pessoa, 0, 0, 0, 0, 0])

leads_novos_row = [str(hoje), len(leads_novos)]

# ============================================================
# ESCREVE NO GOOGLE SHEETS — UPSERT (atualiza a linha de hoje, não duplica)
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


def col_letter(n):
    letras = ""
    while n > 0:
        n, resto = divmod(n - 1, 26)
        letras = chr(65 + resto) + letras
    return letras


def upsert_rows(ws, key_cols_count, rows):
    """Atualiza linhas cuja chave (primeiras `key_cols_count` colunas) já existe;
    cria linha nova quando a chave ainda não apareceu hoje."""
    if not rows:
        return

    existentes = ws.get_all_values()
    corpo = existentes[1:] if len(existentes) > 1 else []

    chave_para_linha = {}
    for i, linha_existente in enumerate(corpo):
        chave = tuple(linha_existente[:key_cols_count])
        chave_para_linha[chave] = i + 2  # +1 cabeçalho, +1 índice base 1

    atualizacoes = []
    novas = []
    for row in rows:
        chave = tuple(str(v) for v in row[:key_cols_count])
        if chave in chave_para_linha:
            atualizacoes.append((chave_para_linha[chave], row))
        else:
            novas.append(row)

    for numero_linha, valores in atualizacoes:
        rng = f"A{numero_linha}:{col_letter(len(valores))}{numero_linha}"
        ws.update(range_name=rng, values=[valores], value_input_option="USER_ENTERED")

    if novas:
        ws.append_rows(novas, value_input_option="USER_ENTERED")


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

upsert_rows(ws_pessoa, key_cols_count=2, rows=resumo_pessoa_rows)          # chave: data + pessoa
upsert_rows(ws_bruto, key_cols_count=1, rows=[resumo_bruto_row])          # chave: data
upsert_rows(ws_mix, key_cols_count=3, rows=mix_rows)                      # chave: data + pessoa + tipo
upsert_rows(ws_movimentacao, key_cols_count=3, rows=movimentacao_rows)    # chave: data + pessoa + etapa
upsert_rows(ws_leads_novos, key_cols_count=1, rows=[leads_novos_row])     # chave: data

print(f"Atualizado com sucesso às {agora.strftime('%H:%M')} de {hoje}.")
