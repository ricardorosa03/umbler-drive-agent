# PFA | Umbler → Drive Agent

Recebe webhooks do Umbler Talk, identifica documentos com Claude Vision e salva automaticamente no Google Drive na pasta do cliente.

## Fluxo

```
WhatsApp (cliente envia PDF/foto)
  → Umbler Talk
    → Webhook POST /webhook/umbler?secret=SEU_SECRET
      → Baixa o arquivo do S3 da Umbler
        → Claude Vision identifica o tipo de documento
          → Upload no Drive: /Clientes/{Nome do Cliente}/{arquivo_nomeado}
            → Confirmação via WhatsApp para o cliente
```

## Setup local

```bash
# 1. Instalar dependências
pip install -r requirements.txt

# 2. Configurar variáveis de ambiente
cp .env.example .env
# edite o .env com suas credenciais

# 3. Rodar
uvicorn app.main:app --reload --port 8000
```

## Variáveis de ambiente

| Variável | Descrição |
|---|---|
| `ANTHROPIC_API_KEY` | Chave da API Anthropic |
| `UMBLER_API_TOKEN` | Token Bearer do Umbler Talk (inclua "Bearer ") |
| `UMBLER_ORG_ID` | ID da organização no Umbler |
| `UMBLER_CHANNEL_ID` | ID do canal WhatsApp |
| `WEBHOOK_SECRET` | String secreta para validar chamadas do webhook |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | JSON da Service Account do Google (em uma linha) |
| `GOOGLE_DRIVE_ROOT_FOLDER_ID` | ID da pasta raiz no Drive |

## Google Drive — Service Account

1. Acesse [Google Cloud Console](https://console.cloud.google.com)
2. Crie um projeto → ative a API do Google Drive
3. Crie uma **Service Account** → gere chave JSON
4. Abra a pasta raiz no Drive e **compartilhe com o e-mail da Service Account** (permissão de Editor)
5. Cole o JSON inteiro (em uma linha) na variável `GOOGLE_SERVICE_ACCOUNT_JSON`

## Configurar webhook no Umbler Talk

1. Painel Umbler Talk → Configurações → Webhooks → Novo webhook
2. URL: `https://SUA-URL.railway.app/webhook/umbler?secret=SEU_WEBHOOK_SECRET`
3. Eventos: marque **`MessageFileUploaded`** (e opcionalmente `Message`)
4. Canal: selecione o canal WhatsApp do escritório

## Deploy no Railway

```bash
# 1. Instale Railway CLI
npm install -g @railway/cli

# 2. Login e deploy
railway login
railway init
railway up

# 3. Configure as variáveis de ambiente no painel do Railway
# 4. Copie a URL gerada e cadastre no Umbler Talk
```

## Deploy no Render

1. Crie novo **Web Service** no Render apontando para este repositório
2. Build command: `pip install -r requirements.txt`
3. Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. Configure as variáveis de ambiente no painel

## Teste manual

```bash
# Simula payload do Umbler Talk
curl -X POST "http://localhost:8000/webhook/umbler?secret=SEU_SECRET" \
  -H "Content-Type: application/json" \
  -d '{
    "Type": "MessageFileUploaded",
    "EventId": "teste-001",
    "Payload": {
      "Content": {
        "Contact": {
          "Name": "João Pedro Lemos",
          "PhoneNumber": "+5548999999999",
          "Id": "abc123"
        },
        "Message": {
          "MessageType": "File",
          "File": {
            "Url": "https://URL_DO_ARQUIVO_DE_TESTE",
            "ContentType": "application/pdf",
            "OriginalName": "contrato.pdf",
            "OriginalSizeBytes": 50000
          }
        },
        "Id": "chat-abc123"
      }
    }
  }'
```

## Estrutura de pastas no Drive

```
📁 [Pasta raiz configurada]
  └── 📁 Clientes
        ├── 📁 João Pedro Lemos
        │     ├── CONTRATO_JOAO_PEDRO_LEMOS_2026-05-29.pdf
        │     ├── RG_JOAO_PEDRO_LEMOS_2026-05-29.jpg
        │     └── COMPROVANTE_RESIDENCIA_JOAO_PEDRO_LEMOS_2026-05-30.pdf
        └── 📁 Maria Silva
              └── CPF_MARIA_SILVA_2026-05-29.jpg
```
