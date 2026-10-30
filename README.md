# Reconciliação Neon

Aplicação para receber o relatório customizado do Projuris em Excel, consolidar casos e reconciliá-los com `ai_deployment.neon_reconciliation`.

## Rodar localmente

Requer Python 3.12 e uma URL de conexão PostgreSQL com permissão de `SELECT`, `INSERT` e `UPDATE` na tabela.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export ENTER_POSTGRES_AID_URL='postgresql://usuario:senha@host:porta/banco'
python -m app.server
```

Abra [http://localhost:8000](http://localhost:8000). Para mudar a porta, defina `PORT`. Sem `ENTER_POSTGRES_AID_URL`, o site abre, mas o processamento informa que a conexão precisa ser configurada. Não coloque credenciais no repositório.

## Fluxo

1. Envie um `.xlsx` do relatório customizado do Projuris (até 25 MB). A prévia lê o arquivo em memória e consulta somente os casos ativos (`deleted_at IS NULL`). CNJs inválidos são exibidos, mas ignorados na consolidação.
2. Confira os casos ausentes. Eles são mantidos por padrão; ative **Remover caso** e selecione uma data quando a remoção for necessária.
3. Ao concluir, a aplicação reconsulta os casos ativos e aplica tudo em uma transação: insere casos novos sem atualizar linhas existentes, atualiza somente os campos alterados e `updated_at` dos casos modificados, e grava `deleted_at` no dia escolhido às 18:00:00 com fuso UTC−03:00. Uma falha no banco causa rollback de toda a transação.
4. Após o commit, o arquivo e o resumo são salvos no histórico. Prévia e cancelamento não entram no histórico. Caso o arquivamento falhe depois do commit, a interface informa que as alterações foram aplicadas, mas o histórico não pôde ser salvo.

`data/` é ignorado pelo Git. Configure `DATA_DIR` para apontar para um volume persistente no Railway se o histórico de arquivos precisar sobreviver a reinicializações e deploys. Entradas antigas em `data/history.json` continuam disponíveis para download.

## Railway

O [Procfile](Procfile) inicia o servidor na porta `PORT`. Configure `ENTER_POSTGRES_AID_URL` nas variáveis do serviço da aplicação com uma URL que tenha permissão de leitura e escrita na tabela. A aplicação não executa `DELETE` físico nem `TRUNCATE`.

## Origem da consolidação

`app/consolidation/` foi reutilizado de `Desktop/AID/Neon/reconciliation/src/consolidation/`: mapeamento de colunas, limpeza de dados, validação de CNJ e CPF/CNPJ, consolidação de requisições e documentos. A persistência segue a mesma estrutura de colunas e a mesma adaptação de campos JSON do script original.
