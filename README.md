# Reconciliação Neon

Aplicação para receber o relatório customizado do Projuris em Excel, consolidar casos e comparar o resultado com `ai_deployment.neon_reconciliation`. Nesta etapa, a conexão com PostgreSQL é **somente leitura**: a interface mostra uma prévia das inclusões, atualizações e remoções, sem aplicar nenhuma delas.

## Rodar localmente

Requer Python 3.12 e uma URL de conexão PostgreSQL com permissão de leitura da tabela.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export ENTER_POSTGRES_AID_URL='postgresql://usuario:senha@host:porta/banco'
python -m app.server
```

Abra [http://localhost:8000](http://localhost:8000). Para mudar a porta, defina `PORT`. Sem `ENTER_POSTGRES_AID_URL`, o site abre, mas o processamento informa que a conexão precisa ser configurada. Não coloque credenciais no repositório.

## Fluxo de prévia

1. Envie um `.xlsx` do relatório customizado do Projuris (até 25 MB). O arquivo é lido em memória e não é salvo.
2. A aplicação consulta os casos ativos da tabela (`deleted_at IS NULL`) em uma transação PostgreSQL `READ ONLY`, compara por `legal_folder` e mostra os possíveis casos novos, alterados e ausentes. CNJs inválidos são exibidos, mas ignorados na consolidação.
3. Um caso ausente é mantido por padrão. O toggle **Remover caso** permite simular uma remoção com data selecionada. O balanço final mostra apenas o que seria inserido, atualizado ou removido; nenhuma alteração é aplicada.

O cadastro simulado, o arquivamento de novos arquivos e a gravação de novas entradas no histórico estão desativados. Se já houver um `data/history.json` de uma versão anterior, ele continua disponível apenas para leitura, inclusive para baixar arquivos antigos em `data/uploads/`. Novas prévias não entram no histórico. `data/` é ignorado pelo Git.

## Railway

O [Procfile](Procfile) inicia o servidor na porta `PORT`. Configure `ENTER_POSTGRES_AID_URL` nas variáveis do **serviço da aplicação** com a URL do PostgreSQL que contém `ai_deployment.neon_reconciliation`. Recomenda-se usar uma credencial de banco limitada a `SELECT`. A conexão também define cada transação como `READ ONLY`; o código executa somente uma consulta `SELECT` na tabela.

## Origem da consolidação

`app/consolidation/` foi reutilizado de `Desktop/AID/Neon/reconciliation/src/consolidation/`: mapeamento de colunas, limpeza de dados, validação de CNJ e CPF/CNPJ, consolidação de requisições e documentos. A interface usa a identidade visual Enter/Neon, com componentes inspirados no Aider.
