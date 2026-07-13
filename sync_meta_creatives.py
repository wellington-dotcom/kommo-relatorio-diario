"""
sync_meta_creatives.py

Puxa os anúncios ativos/pausados da conta de anúncios Meta e monta um mapa
local (creative_map.json) de:

    código de referência  ->  dados do anúncio/campanha/criativo

CONVENÇÃO OBRIGATÓRIA:
Todo anúncio precisa ter, no NOME do anúncio (dentro do Ads Manager), um
código entre colchetes no início. Ex:

    [CR014] Dr. Ricardo - Reels emagrecimento

Esse mesmo código (CR014) precisa estar na mensagem pré-preenchida do
Click-to-WhatsApp daquele anúncio, porque é isso que chega na primeira
mensagem do lead e é o que o script de atribuição vai procurar.

Rodar 1x/dia (ou sob demanda) via GitHub Actions. Ver
.github/workflows/sync_creatives.yml
"""

import os
import re
import json
import requests

META_ACCESS_TOKEN = os.environ["META_ACCESS_TOKEN"]
# Aceita uma ou várias contas separadas por vírgula, com ou sem prefixo "act_"
# Ex: "111111111111111,222222222222222"
_raw_accounts = os.environ["META_AD_ACCOUNT_IDS"]
AD_ACCOUNT_IDS = [
    acc if acc.strip().startswith("act_") else f"act_{acc.strip()}"
    for acc in _raw_accounts.split(",")
    if acc.strip()
]
GRAPH_VERSION = os.environ.get("META_GRAPH_VERSION", "v23.0")
OUTPUT_PATH = os.environ.get("CREATIVE_MAP_PATH", "creative_map.json")

# Código: 2 a 4 letras maiúsculas seguidas de 2 a 5 dígitos. Ex: CR014, AKR2201
CODE_PATTERN = re.compile(r"\[([A-Z]{2,4}\d{2,5})\]")


def fetch_ads(account_id):
    """Busca todos os anúncios de UMA conta, seguindo paginação."""
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{account_id}/ads"
    params = {
        "access_token": META_ACCESS_TOKEN,
        "fields": ",".join(
            [
                "id",
                "name",
                "status",
                "campaign{id,name}",
                "adset{id,name}",
                "creative{id,name,thumbnail_url,image_url,video_id}",
            ]
        ),
        "effective_status": json.dumps(["ACTIVE", "PAUSED"]),
        "limit": 50,
    }

    ads = []
    next_url = url
    next_params = params
    while next_url:
        resp = requests.get(next_url, params=next_params, timeout=30)
        if not resp.ok:
            print(f"Erro da API do Meta (status {resp.status_code}) para {account_id}:")
            print(resp.text)
        resp.raise_for_status()
        payload = resp.json()
        ads.extend(payload.get("data", []))
        next_url = payload.get("paging", {}).get("next")
        next_params = None  # a URL de "next" já vem com todos os parâmetros
    return ads


def resolve_video_url(video_id):
    """Pega a URL fonte de um vídeo de anúncio, se o criativo for vídeo."""
    if not video_id:
        return None
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{video_id}"
    params = {"access_token": META_ACCESS_TOKEN, "fields": "source,picture"}
    resp = requests.get(url, params=params, timeout=30)
    if resp.ok:
        return resp.json().get("source")
    return None


def build_map(ads, account_id):
    creative_map = {}
    skipped = []

    for ad in ads:
        name = ad.get("name", "")
        match = CODE_PATTERN.search(name)
        if not match:
            skipped.append(name)
            continue

        code = match.group(1)
        creative = ad.get("creative") or {}
        video_url = resolve_video_url(creative.get("video_id"))

        creative_map[code] = {
            "account_id": account_id,
            "ad_id": ad["id"],
            "ad_name": name,
            "campaign_name": (ad.get("campaign") or {}).get("name"),
            "adset_name": (ad.get("adset") or {}).get("name"),
            "creative_name": creative.get("name"),
            "thumbnail_url": creative.get("thumbnail_url"),
            "image_url": creative.get("image_url"),
            "video_url": video_url,
            "status": ad.get("status"),
        }

    return creative_map, skipped


def main():
    creative_map = {}
    all_skipped = []

    for account_id in AD_ACCOUNT_IDS:
        print(f"Buscando anúncios da conta {account_id}...")
        ads = fetch_ads(account_id)
        print(f"  -> {len(ads)} anúncios encontrados")
        account_map, skipped = build_map(ads, account_id)
        creative_map.update(account_map)
        all_skipped.extend(skipped)

    skipped = all_skipped

    # Faz merge com o mapa existente em vez de sobrescrever (preserva
    # códigos de anúncios antigos/pausados que já saíram da consulta).
    if os.path.exists(OUTPUT_PATH):
        with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
            existing = json.load(f)
        existing.update(creative_map)
        creative_map = existing

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(creative_map, f, ensure_ascii=False, indent=2)

    print(f"{len(creative_map)} códigos mapeados em {OUTPUT_PATH}")
    if skipped:
        print(f"\n{len(skipped)} anúncios SEM código no nome (ignorados):")
        for name in skipped:
            print(f"  - {name}")


if __name__ == "__main__":
    main()
