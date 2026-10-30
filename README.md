# Reconciliação Neon

Aplicação simples para receber uma extração XLSX do ProJuris, consolidar casos e revisar diferenças antes de aplicar alterações. A interface usa a identidade visual Enter/Neon, com componentes inspirados no Aider. Nesta etapa, o cadastro é **simulado** em um arquivo JSON local; não há conexão com banco de dados nem login.

## Rodar localmente

Requer Python 3.12 (ou 3.9+ para execução local).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.server
```

Abra [http://localhost:8000](http://localhost:8000). Para mudar a porta, defina `PORT`.

## Fluxo

1. Envie o relatório customizado do Projuris em `.xlsx` (até 25 MB). Ao clicar em **Continuar**, a área de upload mostra o progresso enquanto o servidor guarda o arquivo temporariamente e consolida os casos.
2. Se houver casos ativos ausentes da extração ou CNJs inválidos, a tela de revisão abre automaticamente. Um caso ausente é mantido por padrão. Ative **Remover caso** para marcar a remoção; a data local de hoje já vem selecionada no calendário e pode ser alterada. Ela vira `deleted_at`. Casos com CNJ inválido são exibidos e não são inseridos ou atualizados.
3. Se não houver nada a revisar, a reconciliação finaliza automaticamente. O balanço mostra casos consolidados, inseridos, atualizados, removidos, sem alterações e com CNJ inválido. A ação é aplicada apenas a `data/mock_cases.json`. Use **Nova reconciliação** para voltar ao início. **Cancelar** na revisão também reinicia o fluxo e descarta a tentativa.

Ao iniciar pela primeira vez, `data/mock_cases.json` recebe dois casos de exemplo de [app/mock_cases.example.json](app/mock_cases.example.json). Edite esse arquivo local para simular outro estado do cadastro. A comparação usa somente registros com `deleted_at` vazio. Casos rejeitados por CNJ inválido continuam contando como presentes no relatório e não aparecem indevidamente entre os ausentes. Somente reconciliações concluídas entram em `data/history.json`; seus arquivos são arquivados com timestamp UTC e identificador único em `data/uploads/`. Arquivos inválidos e tentativas canceladas não entram no histórico. O histórico exibe inseridos, atualizados, removidos, sem alterações e com CNJ inválido nessa ordem, em páginas de 5 itens. Registros antigos concluídos são adaptados automaticamente para esse formato ao iniciar o servidor.

Os arquivos em `data/` são ignorados pelo Git. Para manter o histórico em Railway entre deploys e reinícios, monte um volume persistente e defina `DATA_DIR` para o ponto de montagem. Sem volume, o armazenamento local é temporário.

## Railway

O [Procfile](Procfile) inicia o servidor na porta fornecida por `PORT`. Configure a instalação com `pip install -r requirements.txt`. Login e banco real ficam para a próxima etapa.

## Origem da consolidação

`app/consolidation/` foi reutilizado de `Desktop/AID/Neon/reconciliation/src/consolidation/`: mapeamento de colunas, limpeza de dados, validação de CNJ e CPF/CNPJ, consolidação de requisições e documentos. O backend desta aplicação adiciona o arquivamento, a comparação com o cadastro simulado e a revisão de remoções. Os estilos, a fonte Geist e a marca visual foram baseados em `Desktop/AID/aider`.
