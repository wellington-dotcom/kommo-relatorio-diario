# Atribuição de criativo Meta → lead no Kommo

Fecha o elo **clique no anúncio → conversa no Kommo (via WhatsApp Lite) → venda**,
sem depender do `ctwa_clid` (que só existe na WhatsApp Cloud API oficial, e o
WhatsApp Lite do Kommo não passa por ela).

## Como funciona, resumido

1. Cada anúncio no Ads Manager leva um **código** no nome, ex: `[CR014] ...`
2. A mensagem pré-preenchida do Click-to-WhatsApp daquele anúncio contém o
   mesmo código (ex: "Olá! Vim pelo anúncio CR014, quero saber mais 😊").
3. Quando o lead manda essa mensagem, o Kommo dispara um webhook.
4. Um Worker do Cloudflare recebe esse webhook e repassa pro GitHub Actions.
5. O GitHub Actions roda um script que lê o código na mensagem, busca os
   dados do anúncio (campanha, criativo, link do vídeo/imagem) num mapa
   local, e atualiza o lead no Kommo com campos customizados + uma nota.
6. Separadamente, todo dia, outro script atualiza esse mapa puxando os
   anúncios ativos direto da API do Meta.

```
Meta Ads Manager                Kommo (WhatsApp Lite)
      |                                |
      | (nome do anúncio =             | (lead manda 1ª mensagem
      |  código + msg pré-preenchida)  |  com o código dentro)
      v                                v
sync_meta_creatives.py  --->  creative_map.json  <---  process_lead_attribution.py
      ^                                                        ^
      | roda 1x/dia (cron)                                     | disparado por
      | via GitHub Actions                                     | repository_dispatch
                                                                 |
                                                    cloudflare_worker.js
                                                    (recebe o webhook do Kommo)
```

## Passo a passo de configuração

### 1. Criar os campos customizados no Kommo

Configurações da conta → Leads → Campos personalizados → criar 4 campos de
texto (tipo "Texto" ou "Texto longo" pro link):

- `ID do Anúncio`
- `Campanha`
- `Anúncio`
- `Link do Criativo`

Depois, pegue o **ID numérico** de cada um com:

```
GET https://{subdomain}.kommo.com/api/v4/leads/custom_fields
Authorization: Bearer {seu token}
```

Esses IDs vão virar os secrets `KOMMO_FIELD_AD_ID`, `KOMMO_FIELD_CAMPANHA`,
`KOMMO_FIELD_ANUNCIO`, `KOMMO_FIELD_LINK_CRIATIVO`.

### 2. Convenção de nome nos anúncios (Meta Ads Manager)

Ao criar/duplicar um anúncio:

- Nome do anúncio: `[CR014] Descrição livre do anúncio`
- Mensagem pré-preenchida do CTWA (na configuração do destino
  Click-to-WhatsApp): inclua o texto `CR014` em algum lugar.

Sugestão de sequência de códigos: `CR` para Notus/clínicas, `AK` para
Akron, seguido de um número — mas fica a seu critério, o script só exige o
padrão `[LETRAS+NÚMEROS]` no nome e `LETRAS+NÚMEROS` na mensagem.

### 3. Configurar o webhook no Kommo

Configurações → Integrações → Webhooks → Adicionar webhook:

- URL: a URL do seu Worker Cloudflare (passo 5)
- Evento: **Mensagem recebida** (incoming message)

### 4. Repositório GitHub

Crie um repositório privado com esta pasta e configure os *secrets*
(Settings → Secrets and variables → Actions):

| Secret | Descrição |
|---|---|
| `META_ACCESS_TOKEN` | Token de sistema (long-lived) com permissão `ads_read` nas contas de anúncios |
| `META_AD_ACCOUNT_IDS` | Uma ou mais contas separadas por vírgula, com ou sem `act_`. Ex: `309252833499468,948696804446934` |
| `KOMMO_SUBDOMAIN` | Ex: `suaempresa` (de `suaempresa.kommo.com`) |
| `KOMMO_TOKEN` | Já existe no seu repo (reaproveitado do relatório diário) |
| `KOMMO_FIELD_AD_ID` / `KOMMO_FIELD_CAMPANHA` / `KOMMO_FIELD_ANUNCIO` / `KOMMO_FIELD_LINK_CRIATIVO` | IDs do passo 1 |

Gere também um **PAT do GitHub** (Settings → Developer settings → Personal
access tokens) com escopo `repo`, para o Worker disparar o
`repository_dispatch`.

### 5. Deploy do Cloudflare Worker

```bash
npm install -g wrangler
wrangler login
wrangler secret put GITHUB_TOKEN     # cole o PAT do passo 4
wrangler deploy
```

Edite `GITHUB_OWNER` e `GITHUB_REPO` no topo do `cloudflare_worker.js` antes
do deploy. A URL gerada (`https://algo.workers.dev`) é a que você cadastra
no webhook do Kommo (passo 3).

### 6. Primeira sincronização

Rode manualmente pelo GitHub (aba Actions → "Sincronizar criativos Meta" →
Run workflow) pra popular o `creative_map.json` antes de qualquer lead
chegar.

## Limitações conhecidas

- **~10% de perda**: se o lead apagar a mensagem pré-preenchida antes de
  enviar, o código some e o lead fica sem atribuição automática (ainda dá
  pra visualizar manualmente qual anúncio pelo horário/canal, mas não
  automatiza). Na prática é a margem que se aceita com WhatsApp Lite.
- **Migração futura**: se algum dia migrar pro WhatsApp Business API
  oficial da Kommo, o `ctwa_clid` passa a vir automaticamente no `referral`
  da mensagem — nesse caso dá pra trocar a extração de código por regex
  por leitura direta do `source_id` do anúncio, sem depender da mensagem
  pré-preenchida não ser editada.
- **Rate limit do Kommo**: máximo 7 requisições/segundo — não é um
  problema no seu volume atual, mas fica registrado.

## Usando os dados pra ROI por criativo

Como cada lead ganho/perdido no Kommo já carrega o campo `ID do Anúncio`,
basta incluir essa coluna na sua planilha de relatório existente
(GitHub Actions + Google Sheets do isapaulaeleuterio) e cruzar com o
status do negócio para calcular conversão e ROI por criativo — não só
"conversas iniciadas" como o Ads Manager mostra.
