"""
Relatorio Meta Ads -> Google Sheets, feito para rodar via GitHub Actions.

Variaveis de ambiente esperadas (vem dos Secrets do repositorio):
    GOOGLE_CREDENTIALS   - JSON da service account do Google (conteudo inteiro do arquivo)
    META_ACCESS_TOKEN    - token de acesso da Marketing API do Meta
    META_AD_ACCOUNT_IDS  - IDs das contas de anuncio separados por virgula
                            (ex: "111111111111111,222222222222222")
    SHEET_ID             - ID da planilha do Google Sheets (parte da URL)
    MODE                 - "daily" (padrao) ou "backfill"
    SINCE / UNTIL         - datas YYYY-MM-DD, usadas apenas quando MODE=backfill

Modo de uso local (fora do GitHub Actions), exportando as variaveis antes:
    export GOOGLE_CREDENTIALS="$(cat service_account.json)"
    export META_ACCESS_TOKEN="..."
    export META_AD_ACCOUNT_IDS="111111111111111,222222222222222"
    export SHEET_ID="..."
    python meta_ads_report_github.py               # roda o dia anterior
    MODE=backfill SINCE=2026-07-01 UNTIL=2026-07-11 python meta_ads_report_github.py
"""

import json
import os
import sys
import time
from datetime import date, timedelta

import gspread
import requests
from google.oauth2.service_account import Credentials

API_VERSION = 'v23.0'
SHEET_NAME = 'Meta Ads Report'

# Todos os campos validos testados diretamente na API (nivel anuncio).
FIELDS = [
    'account_id', 'account_name', 'campaign_id', 'campaign_name', 'adset_id', 'adset_name', 'ad_id', 'ad_name',
    'date_start', 'date_stop', 'objective', 'optimization_goal', 'buying_type', 'attribution_setting',
    'impressions', 'reach', 'frequency', 'spend', 'social_spend', 'full_view_impressions', 'full_view_reach',
    'clicks', 'ctr', 'inline_link_clicks', 'inline_link_click_ctr', 'outbound_clicks', 'outbound_clicks_ctr',
    'cpc', 'cpm', 'cpp', 'cost_per_inline_link_click', 'cost_per_outbound_click', 'cost_per_unique_click',
    'cost_per_unique_outbound_click', 'cost_per_unique_inline_link_click', 'cost_per_thruplay',
    'cost_per_estimated_ad_recallers', 'cost_per_action_type', 'cost_per_conversion', 'cost_per_unique_action_type',
    'cost_per_result', 'cost_per_objective_result', 'inline_post_engagement', 'cost_per_inline_post_engagement',
    'estimated_ad_recallers', 'estimated_ad_recall_rate', 'actions', 'action_values', 'conversions',
    'conversion_values', 'purchase_roas', 'website_purchase_roas', 'mobile_app_purchase_roas', 'objective_results',
    'objective_result_rate', 'results', 'result_rate', 'result_values_performance_indicator', 'video_play_actions',
    'video_p25_watched_actions', 'video_p50_watched_actions', 'video_p75_watched_actions', 'video_p95_watched_actions',
    'video_p100_watched_actions', 'video_avg_time_watched_actions', 'video_30_sec_watched_actions',
    'video_6_sec_watched_actions', 'cost_per_6_sec_video_view', 'quality_ranking', 'engagement_rate_ranking',
    'conversion_rate_ranking', 'website_ctr', 'total_card_view', 'canvas_avg_view_percent', 'canvas_avg_view_time',
    'instagram_profile_visits', 'instant_experience_clicks_to_open', 'instant_experience_clicks_to_start',
    'instant_experience_outbound_clicks',
]


def get_env(name, required=True, default=None):
    val = os.environ.get(name, default)
    if required and not val:
        sys.exit(f'ERRO: variavel de ambiente {name} nao configurada.')
    return val


def get_gspread_client():
    creds_raw = get_env('GOOGLE_CREDENTIALS')
    try:
        creds_info = json.loads(creds_raw)
    except json.JSONDecodeError:
        sys.exit('ERRO: GOOGLE_CREDENTIALS nao e um JSON valido de service account.')
    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    credentials = Credentials.from_service_account_info(creds_info, scopes=scopes)
    return gspread.authorize(credentials)


def get_accounts():
    raw = get_env('META_AD_ACCOUNT_IDS')
    return [a.strip().lstrip('act_') for a in raw.split(',') if a.strip()]


def _get_with_retry(session, url, params=None, max_attempts=5):
    last_data = None
    for attempt in range(1, max_attempts + 1):
        resp = session.get(url, params=params, timeout=90)
        try:
            data = resp.json()
        except ValueError:
            data = {'error': {'message': f'Resposta invalida (status {resp.status_code})'}}
        if 'error' not in data:
            return data
        last_data = data
        msg = data['error'].get('message', '')
        code = data['error'].get('code')
        if code in (1, 2, 4, 17, 32, 613) or 'unknown error' in msg.lower():
            wait = min(2 ** attempt, 30)
            print(f'  aviso: erro transitorio ({msg}) - tentando de novo em {wait}s (tentativa {attempt}/{max_attempts})')
            time.sleep(wait)
            continue
        break
    raise RuntimeError(f"Erro: {last_data['error'].get('message')}")


def fetch_insights(session, token, account_id, since, until):
    url = f'https://graph.facebook.com/{API_VERSION}/act_{account_id}/insights'
    params = {
        'level': 'ad',
        'time_increment': 1,
        'time_range': json.dumps({'since': since, 'until': until}),
        'fields': ','.join(FIELDS),
        'limit': 100,
        'access_token': token,
    }
    rows = []
    data = _get_with_retry(session, url, params=params)
    rows.extend(data.get('data', []))
    next_url = data.get('paging', {}).get('next')
    while next_url:
        data = _get_with_retry(session, next_url)
        rows.extend(data.get('data', []))
        next_url = data.get('paging', {}).get('next')
        time.sleep(0.3)
    return rows


def flatten_row(row):
    out = {}
    for k, v in row.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            for item in v:
                sub_type = item.get('action_type') or item.get('indicator') or item.get('result_type') or 'value'
                out[f'{k}__{sub_type}'] = item.get('value', json.dumps(item))
        elif isinstance(v, (list, dict)):
            out[k] = json.dumps(v)
        else:
            out[k] = v
    return out


def rows_to_grid(flat_rows):
    headers = []
    for r in flat_rows:
        for k in r.keys():
            if k not in headers:
                headers.append(k)
    grid = [headers]
    for r in flat_rows:
        grid.append([str(r.get(h, '')) for h in headers])
    return headers, grid


def get_or_create_worksheet(sh):
    try:
        return sh.worksheet(SHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        return sh.add_worksheet(title=SHEET_NAME, rows=1000, cols=150)


def run_backfill(session, token, accounts, since, until, sh):
    all_rows = []
    for acc in accounts:
        print(f'Buscando conta {acc} ({since} a {until})...')
        rows = fetch_insights(session, token, acc, since, until)
        print(f'  -> {len(rows)} linhas')
        all_rows.extend(rows)

    flat_rows = [flatten_row(r) for r in all_rows]
    headers, grid = rows_to_grid(flat_rows)
    print(f'Total: {len(flat_rows)} linhas, {len(headers)} colunas')

    ws = get_or_create_worksheet(sh)
    ws.clear()
    ws.update(grid, 'A1')
    ws.freeze(rows=1)
    print('Planilha atualizada:', sh.url)


def run_daily(session, token, accounts, sh):
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    daily_rows = []
    for acc in accounts:
        rows = fetch_insights(session, token, acc, yesterday, yesterday)
        daily_rows.extend(rows)

    daily_flat = [flatten_row(r) for r in daily_rows]

    if not daily_flat:
        print(f'Nenhum dado retornado para {yesterday}.')
        return

    ws = get_or_create_worksheet(sh)
    existing_headers = ws.row_values(1)

    if not existing_headers:
        headers, grid = rows_to_grid(daily_flat)
        ws.update(grid, 'A1')
        ws.freeze(rows=1)
        print(f'{len(daily_flat)} linhas escritas (planilha estava vazia) para {yesterday}.')
        return

    new_cols = []
    for r in daily_flat:
        for k in r.keys():
            if k not in existing_headers and k not in new_cols:
                new_cols.append(k)
    if new_cols:
        ws.update([existing_headers + new_cols], 'A1')
        existing_headers = existing_headers + new_cols

    values = [[str(r.get(h, '')) for h in existing_headers] for r in daily_flat]
    ws.append_rows(values, value_input_option='RAW')
    print(f'{len(values)} linhas adicionadas para {yesterday}.')


def main():
    mode = os.environ.get('MODE', 'daily').strip().lower()
    token = get_env('META_ACCESS_TOKEN')
    accounts = get_accounts()
    sheet_id = get_env('SHEET_ID')

    gc = get_gspread_client()
    sh = gc.open_by_key(sheet_id)

    session = requests.Session()

    if mode == 'backfill':
        since = get_env('SINCE')
        until = get_env('UNTIL')
        run_backfill(session, token, accounts, since, until, sh)
    else:
        run_daily(session, token, accounts, sh)


if __name__ == '__main__':
    main()
