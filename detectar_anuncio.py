"""
detectar_anuncio.py

Le a mensagem recebida de um lead (via webhook do Kommo, repassado pelo
Cloudflare Worker) e, se o texto mencionar "anuncio" ou variacoes
proximas, adiciona uma tag no lead no Kommo: "Veio de Anúncio".

NAO identifica qual anuncio/criativo especifico - so sinaliza que a
pessoa mencionou vir de um anuncio. Nao depende de nenhuma API do Meta,
nao precisa de codigo em anuncio nenhum.

Disparado pelo workflow .github/workflows/detectar_anuncio.yml, acionado
via repository_dispatch (mesmo Cloudflare Worker que ja recebe o webhook
de mensagem do Kommo).
"""

import os
import sys
import json
import requests

KOMMO_SUBDOMAIN = os.environ["KOMMO_SUBDOMAIN"]
KOMMO_TOKEN = os.environ["KOMMO_TOKEN"]

TAG_NOME = "Veio de Anúncio"

# Palavras que indicam que a pessoa esta falando de anuncio. Ajuste essa
# lista conforme for vendo outras formas de dizer a mesma coisa (gírias,
# erros de digitação comuns, etc.)
PALAVRAS_CHAVE = ["anuncio", "anúncio", "propaganda", "publicidade"]

BASE_URL = f"https://{KOMMO_SUBDOMAIN}.kommo.com"
HEADERS = {
    "Authorization": f"Bearer {KOMMO_TOKEN}",
    "Content-Type": "application/json",
}


def menciona_anuncio(texto):
    if not texto:
        return False
    texto_normalizado = texto.lower()
    return any(palavra in texto_normalizado for palavra in PALAVRAS_CHAVE)


def adicionar_tag(lead_id):
    body = [{"id": lead_id, "tags_to_add": [{"name": TAG_NOME}]}]
    resp = requests.patch(
        f"{BASE_URL}/api/v4/leads", headers=HEADERS, json=body, timeout=30
    )
    resp.raise_for_status()


def main():
    evento = json.loads(sys.argv[1])

    texto = evento.get("text", "")
    lead_id = evento.get("entity_id")
    entity_type = evento.get("entity_type")
    tipo_mensagem = evento.get("type")

    if tipo_mensagem != "incoming" or entity_type != "lead" or not lead_id:
        print("Evento ignorado (não é mensagem recebida associada a um lead).")
        return

    if not menciona_anuncio(texto):
        print(f"Lead {lead_id}: mensagem não menciona anúncio. Texto: {texto!r}")
        return

    adicionar_tag(lead_id)
    print(f"Lead {lead_id} marcado com a tag '{TAG_NOME}'.")


if __name__ == "__main__":
    main()
