import os
import json
import re

import requests
import pandas as pd
import pytz
from datetime import datetime, timedelta, time

import gspread
from google.oauth2.service_account import Credentials

# ============================================================
# CONFIG (vem das "Secrets" do GitHub, NUNCA escrito aqui direto)
# ============================================================
SUBDOMINIO = os.environ["KOMMO_SUBDOMAIN"]
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
ABA_TEMPO_RESPOSTA = "Tempo de Resposta"
ABA_LOG_LEADS = "Log de Movimentação de Leads"

# Palavras-chave usadas pra achar sozinho qual campo personalizado guarda a
# data/hora do agendamento. Se a coluna "horario_agendamento" sair vazia,
# veja o print "Campos identificados como agendamento" no início da execução
# e ajuste esta lista com o nome exato do campo cadastrado no Kommo.
PALAVRAS_CHAVE_AGENDAMENTO = ["agendamento", "consulta marcada", "data e hora", "horário marcado", "atendimento"]

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
# COLETA: status E nome de TODOS os pipelines (corrige o bug do "?" e
# agora também identifica o funil de cada lead, pra coluna 'funil')
# ============================================================
def get_pipelines_e_status():
    status_map = {}
    pipeline_nomes = {}
    rr = requests.get(f"{BASE_URL}/leads/pipelines", headers=HEADERS)
    rr.raise_for_status()
    pipelines = rr.json().get("_embedded", {}).get("pipelines", [])
    for p in pipelines:
        pipeline_nomes[p["id"]] = p.get("name", f"Funil {p['id']}")
        for s in p.get("_embedded", {}).get("statuses", []):
            status_map[s["id"]] = s["name"]
    return status_map, pipeline_nomes


STATUS, PIPELINE_NOMES = get_pipelines_e_status()


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


def detectar_campos_agendamento(campos_personalizados):
    encontrados = {}
    for campo_id, nome in campos_personalizados.items():
        nome_lower = nome.lower()
        if any(p in nome_lower for p in PALAVRAS_CHAVE_AGENDAMENTO):
            encontrados[campo_id] = nome
    return encontrados


CAMPOS_AGENDAMENTO = detectar_campos_agendamento(CAMPOS_PERSONALIZADOS)
if CAMPOS_AGENDAMENTO:
    print(f"Campos identificados como agendamento: {list(CAMPOS_AGENDAMENTO.values())}")
else:
    print(
        "Nenhum campo de agendamento identificado automaticamente — a coluna "
        "'horario_agendamento' vai ficar vazia. Ajuste PALAVRAS_CHAVE_AGENDAMENTO "
        "com o nome exato do campo cadastrado no Kommo."
    )


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


def chunk(lista, tamanho):
    lista = list(lista)
    for i in range(0, len(lista), tamanho):
        yield lista[i:i + tamanho]


def get_leads_por_id(lead_ids):
    """Busca leads em lote (nome, pipeline_id, campos personalizados, contato
    principal) — usado pra montar o log detalhado de mudança de etapa."""
    leads_map = {}
    for grupo in chunk(sorted(set(lid for lid in lead_ids if lid)), 200):
        page = 1
        while True:
            query = [("with", "contacts"), ("limit", 250), ("page", page)]
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


def get_contatos_por_id(contato_ids):
    """Busca contatos em lote — usado pra pegar o telefone."""
    contatos_map = {}
    for grupo in chunk(sorted(set(cid for cid in contato_ids if cid)), 200):
        page = 1
        while True:
            query = [("limit", 250), ("page", page)]
            query += [("filter[id][]", cid) for cid in grupo]
            rr = requests.get(f"{BASE_URL}/contacts", headers=HEADERS, params=query)
            if rr.status_code == 204:
                break
            rr.raise_for_status()
            d = rr.json().get("_embedded", {}).get("contacts", [])
            if not d:
                break
            for contato in d:
                contatos_map[contato["id"]] = contato
            if len(d) < 250:
                break
            page += 1
    return contatos_map


def contato_principal_id(lead):
    contatos = lead.get("_embedded", {}).get("contacts", [])
    if not contatos:
        return None
    for c in contatos:
        if c.get("is_main"):
            return c["id"]
    return contatos[0]["id"]


def normalizar_telefone(bruto):
    """Normaliza telefone BR pra '+55 (DD) 9XXXX-XXXX'. Quando o número de
    dígitos não bate com DDD+celular(9) nem DDD+fixo(8) — ex: veio sem DDD,
    é um número internacional, ou tem lixo digitado — devolve os dígitos
    limpos prefixados com '?' pra você identificar na planilha em vez de
    arriscar formatar errado."""
    if not bruto:
        return ""
    digitos = re.sub(r"\D", "", str(bruto))
    if not digitos:
        return ""

    if digitos.startswith("55") and len(digitos) in (12, 13):
        digitos = digitos[2:]

    if len(digitos) == 11:
        ddd, numero = digitos[:2], digitos[2:]
        return f"+55 ({ddd}) {numero[:5]}-{numero[5:]}"
    if len(digitos) == 10:
        ddd, numero = digitos[:2], digitos[2:]
        return f"+55 ({ddd}) {numero[:4]}-{numero[4:]}"

    return f"? {digitos}"


def extrair_telefone(contato):
    if not contato:
        return ""
    numeros = []
    for cfv in contato.get("custom_fields_values") or []:
        if cfv.get("field_code") == "PHONE":
            for v in cfv.get("values") or []:
                valor = v.get("value")
                if valor:
                    numeros.append(normalizar_telefone(valor))
    texto = ", ".join(numeros)
    if not texto:
        return ""
    # apóstrofo na frente força o Google Sheets a gravar como TEXTO puro.
    # Sem isso, USER_ENTERED tenta interpretar "+55 (...)" como fórmula
    # (o "+" no início dispara o parser) e a célula vira #ERROR!.
    # O apóstrofo some sozinho do valor exibido — não aparece na planilha.
    return f"'{texto}"


def formatar_valor_agendamento(bruto):
    if bruto is None or bruto == "":
        return ""
    try:
        ts = int(bruto)
        if ts > 10 ** 8:  # parece timestamp Unix, não um texto/número comum
            return datetime.fromtimestamp(ts, TZ).strftime("%d/%m/%Y %H:%M")
    except (ValueError, TypeError):
        pass
    return str(bruto)


def extrair_horario_agendamento(lead, campos_agendamento):
    for cfv in lead.get("custom_fields_values") or []:
        if cfv.get("field_id") in campos_agendamento:
            valores = cfv.get("values") or []
            if valores:
                return formatar_valor_agendamento(valores[0].get("value"))
    return ""


eventos = get_eventos()
print(f"{len(eventos)} eventos coletados hoje até agora.")

leads_novos = get_leads_novos()
print(f"{len(leads_novos)} leads novos hoje até agora.")


# ============================================================
# LOG DETALHADO: nome, telefone, funil e etapa de cada lead que mudou de
# etapa hoje — junto com o horário do agendamento, quando existir
# ============================================================
mudancas_etapa = [
    e for e in eventos
    if e.get("type") == "lead_status_changed" and e.get("entity_type") == "lead"
]
lead_ids_hoje = {e.get("entity_id") for e in mudancas_etapa}

leads_info = get_leads_por_id(lead_ids_hoje)
contato_ids_hoje = {contato_principal_id(lead) for lead in leads_info.values()}
contatos_info = get_contatos_por_id(contato_ids_hoje)

log_leads_rows = []
for e in mudancas_etapa:
    lead_id = e.get("entity_id")
    lead = leads_info.get(lead_id, {})
    contato = contatos_info.get(contato_principal_id(lead), {})

    uid = e.get("created_by", 0)
    responsavel = usuarios.get(uid, f"User {uid}")

    status_depois_id = status_antes_id = None
    try:
        status_depois_id = e["value_after"][0]["lead_status"]["id"]
    except (IndexError, KeyError, TypeError):
        pass
    try:
        status_antes_id = e["value_before"][0]["lead_status"]["id"]
    except (IndexError, KeyError, TypeError):
        pass

    etapa_destino = STATUS.get(status_depois_id, "Etapa não identificada")
    etapa_anterior = STATUS.get(status_antes_id, "") if status_antes_id else ""
    funil = PIPELINE_NOMES.get(lead.get("pipeline_id"), "Funil não identificado")
    dt_evento = datetime.fromtimestamp(e["created_at"], TZ)

    log_leads_rows.append(
        [
            str(e["id"]),
            dt_evento.strftime("%d/%m/%Y %H:%M"),
            str(lead_id),
            lead.get("name", ""),
            extrair_telefone(contato),
            funil,
            etapa_destino,
            etapa_anterior,
            responsavel,
            extrair_horario_agendamento(lead, CAMPOS_AGENDAMENTO),
        ]
    )

print(f"{len(log_leads_rows)} mudanças de etapa detalhadas hoje (nome, telefone, funil e agendamento).")


# ============================================================
# TEMPO DE RESPOSTA: primeira resposta de cada lead + média geral
# ============================================================
def calcular_tempos_resposta(eventos, usuarios):
    """Retorna duas listas de (nome_pessoa, minutos):
    - primeira_resposta: só a 1a mensagem recebida de cada lead -> 1a resposta enviada
    - toda_resposta: cada mensagem recebida -> próxima resposta enviada (todas as trocas)
    """
    por_lead = {}
    for e in eventos:
        tipo = e.get("type")
        if tipo not in ("incoming_chat_message", "outgoing_chat_message"):
            continue
        if e.get("entity_type") != "lead":
            continue
        lead_id = e.get("entity_id")
        if lead_id is None:
            continue
        por_lead.setdefault(lead_id, []).append(e)

    primeira_resposta = []
    toda_resposta = []

    for lead_id, evs in por_lead.items():
        evs_ordenados = sorted(evs, key=lambda x: x["created_at"])
        pendentes = []
        primeira_ja_computada = False
        for e in evs_ordenados:
            tipo = e.get("type")
            if tipo == "incoming_chat_message":
                pendentes.append(e["created_at"])
            elif tipo == "outgoing_chat_message":
                if pendentes:
                    uid = e.get("created_by", 0)
                    nome = usuarios.get(uid, f"User {uid}")
                    for t_in in pendentes:
                        delta_min = (e["created_at"] - t_in) / 60
                        toda_resposta.append((nome, delta_min))
                    if not primeira_ja_computada:
                        delta_primeira = (e["created_at"] - pendentes[0]) / 60
                        primeira_resposta.append((nome, delta_primeira))
                        primeira_ja_computada = True
                    pendentes = []
    return primeira_resposta, toda_resposta


HORA_INICIO_COMERCIAL = 7
HORA_FIM_COMERCIAL = 19  # até 18:59, não conta 19:00 em diante


def dentro_do_horario_comercial(e):
    dt = datetime.fromtimestamp(e["created_at"], TZ)
    return HORA_INICIO_COMERCIAL <= dt.hour < HORA_FIM_COMERCIAL


eventos_horario_comercial = [e for e in eventos if dentro_do_horario_comercial(e)]

primeira_resposta, toda_resposta = calcular_tempos_resposta(eventos_horario_comercial, usuarios)
print(
    f"{len(primeira_resposta)} primeiras respostas e {len(toda_resposta)} respostas no total "
    f"(considerando só mensagens entre {HORA_INICIO_COMERCIAL}h e {HORA_FIM_COMERCIAL}h)."
)

primeira_por_pessoa = {}
toda_por_pessoa = {}
for nome, delta in primeira_resposta:
    if nome in FOCO:
        primeira_por_pessoa.setdefault(nome, []).append(delta)
for nome, delta in toda_resposta:
    if nome in FOCO:
        toda_por_pessoa.setdefault(nome, []).append(delta)

tempo_resposta_rows = []
for pessoa in FOCO:
    prim = primeira_por_pessoa.get(pessoa, [])
    tod = toda_por_pessoa.get(pessoa, [])
    tempo_resposta_rows.append(
        [
            str(hoje),
            pessoa,
            round(sum(prim) / len(prim), 1) if prim else 0,
            len(prim),
            round(sum(tod) / len(tod), 1) if tod else 0,
            len(tod),
        ]
    )

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
    cria linha nova quando a chave ainda não apareceu hoje.

    Duas otimizações pra não estourar a cota de escrita do Google Sheets:
    1) Se o conteúdo da linha já é idêntico ao que está na planilha, não
       escreve nada (a maioria dos eventos antigos do log não muda mais).
    2) As atualizações que sobrarem vão todas num único batch_update,
       em vez de uma chamada de API por linha."""
    if not rows:
        return

    existentes = ws.get_all_values()
    corpo = existentes[1:] if len(existentes) > 1 else []

    chave_para_linha = {}
    chave_para_valores = {}
    for i, linha_existente in enumerate(corpo):
        chave = tuple(linha_existente[:key_cols_count])
        chave_para_linha[chave] = i + 2
        chave_para_valores[chave] = linha_existente

    atualizacoes = []
    novas = []
    for row in rows:
        chave = tuple(str(v) for v in row[:key_cols_count])
        row_str = [str(v) for v in row]
        if chave in chave_para_linha:
            existente = chave_para_valores.get(chave, [])
            existente_completo = existente + [""] * (len(row_str) - len(existente))
            if existente_completo[:len(row_str)] != row_str:
                atualizacoes.append((chave_para_linha[chave], row))
            # conteúdo igual ao que já está na planilha -> pula, economiza cota
        else:
            novas.append(row)

    if atualizacoes:
        lote = [
            {"range": f"A{numero_linha}:{col_letter(len(valores))}{numero_linha}", "values": [valores]}
            for numero_linha, valores in atualizacoes
        ]
        ws.batch_update(lote, value_input_option="USER_ENTERED")

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
cabecalho_tempo_resposta = [
    "data", "pessoa",
    "primeira_resposta_min (7h-19h)", "qtd_primeiras_respostas",
    "resposta_media_min (7h-19h)", "qtd_respostas_totais",
]
cabecalho_log_leads = [
    "evento_id", "data_hora", "lead_id", "nome_do_lead", "telefone",
    "funil", "etapa_destino", "etapa_anterior", "responsavel", "horario_agendamento",
]

ws_pessoa = get_or_create_ws(sh, ABA_PESSOA, cabecalho_pessoa)
ws_bruto = get_or_create_ws(sh, ABA_BRUTO, cabecalho_bruto)
ws_mix = get_or_create_ws(sh, ABA_MIX, cabecalho_mix)
ws_movimentacao = get_or_create_ws(sh, ABA_MOVIMENTACAO, cabecalho_movimentacao)
ws_leads_novos = get_or_create_ws(sh, ABA_LEADS_NOVOS, cabecalho_leads_novos)
ws_tempo_resposta = get_or_create_ws(sh, ABA_TEMPO_RESPOSTA, cabecalho_tempo_resposta)
ws_log_leads = get_or_create_ws(sh, ABA_LOG_LEADS, cabecalho_log_leads)

upsert_rows(ws_pessoa, key_cols_count=2, rows=resumo_pessoa_rows)
upsert_rows(ws_bruto, key_cols_count=1, rows=[resumo_bruto_row])
upsert_rows(ws_mix, key_cols_count=3, rows=mix_rows)
upsert_rows(ws_movimentacao, key_cols_count=3, rows=movimentacao_rows)
upsert_rows(ws_leads_novos, key_cols_count=1, rows=[leads_novos_row])
upsert_rows(ws_tempo_resposta, key_cols_count=2, rows=tempo_resposta_rows)
upsert_rows(ws_log_leads, key_cols_count=1, rows=log_leads_rows)

print(f"Atualizado com sucesso às {agora.strftime('%H:%M')} de {hoje}.")
