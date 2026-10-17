# Problemas e soluções

Comece pelo log: `sync.log` na raiz do projeto, quando roda pela tarefa agendada, ou a saída de `python src/main.py` no terminal.

## Mensagens do log

| Mensagem | Significado | O que fazer |
|---|---|---|
| `receber: nada novo no server` | O server está na mesma versão que este PC já recebeu. | Nada. |
| `enviar: nada novo neste PC` | A `NEW_MEMORY` é igual ao que o server já tem. | Nada. |
| `enviar: ... o server já tinha esse conteúdo` | O conteúdo era igual ao do server (só datas mudaram). Nenhuma versão nova foi criada. | Nada. |
| `já existe uma sincronização rodando neste PC; saindo` | A tarefa agendada está no meio de uma rodada. | Normal em execução manual. Tente de novo em instantes. |
| `SERVER_URL não definida` | Falta o `.env` ou a linha `SERVER_URL`. | Crie o `.env` ([setup.md](setup.md#4-criar-o-env)). |
| `aviso: rode 'npm ci' em <pasta>` | Uma skill com `package.json` chegou sem `node_modules` (que não é sincronizado). | `cd <pasta>; npm ci` |
| `mesclar: ... complementado(s)` | Mudanças de outro PC foram aplicadas aqui. | Se vier MCP, plugin ou hook novo, veja [Depois de instalar](setup.md#depois-de-instalar). |
| `<arquivo> mudou durante o envio` | O arquivo foi editado entre a leitura e o envio. | Nada: vai na próxima rodada. |
| `[aviso] <pasta> e <pasta> têm conteúdo: junte manualmente` | `~/.claude/<pasta>` é uma pasta real e a do sharing também tem conteúdo. | Mova os arquivos de `~/.claude/<pasta>` para `claude_memory_sharing/<pasta>`, apague a pasta vazia e rode de novo. |

## Códigos HTTP

| Código | Causa | Solução |
|---|---|---|
| `401` | Chave pública ausente ou errada em `CLIENT_PUBLIC_KEYS`, `CLIENT_ID` diferente do nome usado lá, ou relógio do PC errado em mais de 5 min. | Confira as três coisas. No Windows: Configurações → Hora → Sincronizar agora. |
| `409` | O server não tem um conteúdo que o cliente achou que ele tinha. | O cliente reenvia sozinho. Se persistir, apague `SHARING_MEMORY/` e rode de novo. |
| `413` | Envio maior que `MAX_UPLOAD_MB` (padrão 50). | Aumente a variável no server ou tire arquivos grandes do sharing. |
| `415` ou `manifesto inválido (server desatualizado?)` | Cliente e server em versões de protocolo diferentes. | Atualize os dois: `git pull` + `restart_sync.bat`, e deploy do server. |
| `429` | 10 falhas de autenticação em 15 min a partir deste IP. | Corrija o `.env` e espere 15 min. |
| `503` | O server já está processando 2 envios pesados. | Nada: a próxima rodada tenta de novo. |
| `não consegui falar com ...` | Server fora do ar, URL errada ou sem internet. | Confira a `SERVER_URL` e o serviço na Railway. Com o sleep ativo, o primeiro pedido pode demorar alguns segundos. |

## Situações

**Um arquivo que apaguei voltou.**
Esperado: o merge nunca apaga, nem no PC nem no server. O server mantém tudo o que já recebeu e devolve o arquivo aos PCs. Para remover algo de vez:
1. Pare o sync em todos os PCs (`install_autostart.ps1 -Uninstall` ou encerre a tarefa).
2. Apague o arquivo em `~/.claude/claude_memory_sharing` de **todos** os PCs.
3. Zere o histórico do server: `TRUNCATE memory_versions, blobs;` no Postgres (veja a doc do backend).
4. Em **um** PC, apague `.sync_state.json` e `SHARING_MEMORY/` e rode `python src/main.py`. Ele reenvia tudo, já sem o arquivo.
5. Nos outros PCs, apague `.sync_state.json` e reinstale ou reinicie o sync.

**Uma mudança de outro PC não chegou.**
O outro PC precisa enviar primeiro, e depois este precisa receber. Rode `python src/main.py` no outro PC e depois neste. Confira no log do outro PC se apareceu `enviar: ... -> versão N`.

**Quero refazer o recebimento do zero.**
Apague `SHARING_MEMORY/` e `.sync_state.json` e rode `python src/main.py`. O conteúdo que já existe na `NEW_MEMORY` é reaproveitado, e só o resto é baixado.

**Um merge estragou o `settings.json` ou o `~/.claude.json`.**
O merge sempre guarda o anterior em `settings.json.bak-sync` e `.claude.json.bak-sync`. Restaure a partir deles.

**O `CLAUDE.md` global foi substituído.**
O anterior está em `~/.claude/CLAUDE.md.bak-sync`. Conflito de `CLAUDE.md` é resolvido pelo arquivo mais novo, inteiro.

**Um MCP não conecta depois do sync.**
Quase sempre ele veio de outro PC e aponta para um programa que não existe aqui. Veja o `command` em `~/.claude.json` → `mcpServers` e instale o programa. Não apague a entrada, porque o outro PC usa.

**Mudei o código e o sync continua igual.**
A tarefa agendada mantém o processo antigo carregado. Rode `restart_sync.bat`.

**Testar sem afetar os outros PCs.**
- `python src/main.py --no-upload` só recebe e mescla.
- `python src/main.py --offline` só gera a `NEW_MEMORY`, sem rede.
