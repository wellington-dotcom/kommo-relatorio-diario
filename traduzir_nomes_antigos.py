import os
import json

import requests
import gspread
from google.oauth2.service_account import Credentials

# ============================================================
# CONFIG
# ============================================================
SUBDOMINIO = "isapaulaeleuterio"
TOKEN = os.environ["KOMMO_TOKEN"]
SHEET_ID = os.environ["SHEET_ID"]
GOOGLE_CREDS = os.environ["GOOGLE_CREDENTIALS"]

BASE_URL = f"https://{SUBDOMINIO}.kommo.com/api/v4"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

ABA_MIX = "Mix de Tipos por Pessoa"
ABA_MOVIMENTACAO = "Movimentações por Etapa"

# ============================================================
# TRADUÇÃO (igual ao relatorio_diario.py)
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
print(f"{len(CAMPOS_PERSONALIZADOS)} campos personalizados encontrados na conta.")


def nome_tipo(tipo):
    if tipo.startswith("custom_field_") and tipo.endswith("_value_changed"):
        meio = tipo[len("custom_field_"):-len("_value_changed")]
        try:
            campo_id = int(meio)
        except ValueError:
            campo_id = None
        nome_campo = CAMPOS_PERSONALIZADOS.get(campo_id, f"campo personalizado {meio}")
        return f"Campo alterado: {nome_campo}"
    return TIPOS_TRADUZIDOS.get(tipo, tipo)


def nome_etapa(etapa):
    # O ID original da etapa não fica salvo na planilha, então um "?" antigo só
    # pode virar um rótulo mais claro — não dá pra recuperar o nome real da etapa.
    if etapa == "?":
        return "Etapa não identificada"
    return etapa


# ============================================================
# CONECTA NA PLANILHA
# ============================================================
scopes = ["https://www.googleapis.com/auth/spreadsheets"]
creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS), scopes=scopes)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SHEET_ID)


def limpar_aba(ws, funcao_traducao, nome_aba):
    """Traduz a coluna C (índice 2) de cada linha usando `funcao_traducao`.
    Se a tradução fizer duas linhas colidirem na mesma chave (data+pessoa+valor),
    soma as quantidades numa linha só e apaga as duplicadas."""
    valores = ws.get_all_values()
    corpo = valores[1:]  # pula cabeçalho

    grupos = {}  # (data, pessoa, valor_traduzido) -> {"linhas": [...], "soma": int}
    for i, linha in enumerate(corpo, start=2):  # linha 2 = primeira linha de dados
        if len(linha) < 4:
            continue
        data, pessoa, valor_bruto, qtd = linha[0], linha[1], linha[2], linha[3]
        valor_novo = funcao_traducao(valor_bruto)
        try:
            qtd_num = int(qtd)
        except ValueError:
            qtd_num = 0
        chave = (data, pessoa, valor_novo)
        grupo = grupos.setdefault(chave, {"linhas": [], "soma": 0})
        grupo["linhas"].append(i)
        grupo["soma"] += qtd_num

    atualizacoes = []
    linhas_para_apagar = []
    mudou = 0

    for (data, pessoa, valor_novo), info in grupos.items():
        linhas = info["linhas"]
        linha_principal = linhas[0]
        atualizacoes.append(
            {"range": f"A{linha_principal}:D{linha_principal}", "values": [[data, pessoa, valor_novo, info["soma"]]]}
        )
        mudou += 1
        if len(linhas) > 1:
            linhas_para_apagar.extend(linhas[1:])

    if atualizacoes:
        ws.batch_update(atualizacoes, value_input_option="USER_ENTERED")

    for numero_linha in sorted(set(linhas_para_apagar), reverse=True):
        ws.delete_rows(numero_linha)

    print(
        f"'{nome_aba}': {mudou} linhas escritas/atualizadas, "
        f"{len(set(linhas_para_apagar))} linhas duplicadas removidas."
    )


ws_mix = sh.worksheet(ABA_MIX)
limpar_aba(ws_mix, nome_tipo, ABA_MIX)

ws_mov = sh.worksheet(ABA_MOVIMENTACAO)
limpar_aba(ws_mov, nome_etapa, ABA_MOVIMENTACAO)

print("Limpeza concluída.")
