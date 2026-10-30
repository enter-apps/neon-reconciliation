# Reconciliação de casos Neon

Aplicação para reconciliar o cadastro de casos da Neon no PostgreSQL a partir do relatório customizado do Projuris em Excel.

## Como funciona

1. Envie o relatório `.xlsx` (até 25 MB). A aplicação valida o formato, consolida os casos e identifica CNJs inválidos, que não são incluídos na reconciliação.
2. Os casos consolidados são comparados aos registros ativos de `ai_deployment.neon_reconciliation`. Casos ausentes do relatório são mantidos por padrão; para removê-los, ative **Remover caso** e escolha a data.
3. Ao concluir, a aplicação insere casos novos, atualiza os campos alterados dos casos existentes e marca as remoções em `deleted_at`. A remoção usa a data escolhida às 18:00:00 no fuso UTC−03:00. Todas as alterações no banco são feitas em uma transação.
4. O histórico registra apenas reconciliações concluídas, com o resumo dos resultados e o arquivo original para download.

## Executar localmente

Requer Python 3.12 e acesso ao PostgreSQL com permissões de `SELECT`, `INSERT` e `UPDATE` na tabela.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export ENTER_POSTGRES_AID_URL='postgresql://usuario:senha@host:porta/banco'
python -m app.server
```

Abra [http://localhost:8000](http://localhost:8000). Para rodar os testes:

```bash
python -m unittest discover -s tests -v
```

## Configuração

| Variável | Uso |
| --- | --- |
| `ENTER_POSTGRES_AID_URL` | URL do PostgreSQL que contém `ai_deployment.neon_reconciliation`. Obrigatória para processar arquivos. |
| `DATA_DIR` | Diretório do histórico e dos arquivos enviados. Padrão: `data/` na raiz do projeto. No Railway, aponte para um volume persistente para preservar o histórico entre deploys. |
| `PORT` | Porta do servidor. Padrão: `8000`; no Railway, é fornecida pelo ambiente. |

Prévias não concluídas ficam apenas em memória e expiram após uma hora. Não inclua credenciais no repositório.
