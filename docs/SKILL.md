---
name: claude-memory-sync
description: Instala, configura e diagnostica o Claude Memory Sync, que mantém skills, agentes, comandos, hooks, CLAUDE.md, settings, plugins e MCPs do Claude Code iguais entre vários PCs (Windows, macOS, Linux), com merge de 3 vias, propagação de remoções e criptografia no PC, por meio de um server próprio ou de um repositório Git privado. Use quando o usuário pedir para sincronizar o Claude entre computadores, configurar um PC novo no sync, gerar ou trocar a SYNC_KEY, recuperar um arquivo removido ou uma versão perdida num conflito, conferir pendências de MCP/plugin/hook vindas de outro PC, subir o server na Railway, usar o modo Git, ou quando o sync.log mostrar erro (401, 415, 429, SYNC_KEY, --allow-delete, conflito, npm ci).
---

# Claude Memory Sync

Projeto:
- **Cliente** (Python, um por PC): https://github.com/le0nardomartins/ClaudeMemorySync
- **Destino**, um de dois modos, escolhido no `.env` do cliente:
  - **Server** (padrão, `SERVER_URL`): https://github.com/le0nardomartins/ClaudeMemorySync-Server (Node + Postgres, na Railway)
  - **Repositório Git privado** (`SYNC_BACKEND=git` + `GIT_REPO_URL`): sem server. Veja `git-backend.md`.
- **`SYNC_KEY`** (recomendada, igual em todos os PCs): cifra tudo no PC. O destino não lê nem o nome dos arquivos.

Leia sob demanda, nesta pasta:
- `setup.md`: instalação do zero, primeiro PC e PCs seguintes (modo server), autostart nos 3 sistemas
- `git-backend.md`: instalação e operação do modo sem server
- `architecture.md`: rodada, merge de 3 vias, remoções, criptografia, protocolo
- `troubleshooting.md`: mensagens do log, erros e recuperação

## Regras que valem sempre

1. **Segredos.** `.env` (`CLIENT_PRIVATE_KEY`, `SYNC_KEY`), `NEW_MEMORY/`, `SHARING_MEMORY/`, `.sync_conflicts/`, `.sync_trash/`, `settings.template.json` e `mcp.template.json` contêm chaves. Nunca mostre valores de chave ou token no chat e nunca os envie para fora dos PCs do usuário. A `SYNC_KEY` é gerada uma vez e copiada pelo próprio usuário para cada PC: oriente, mas não a transporte você.
2. **Backup antes de mexer.** Copie `~/.claude/settings.json`, `~/.claude.json` e `~/.claude/CLAUDE.md` com o sufixo `.bak-<data>` antes da primeira sincronização de um PC.
3. **Links.** Depois da instalação, `~/.claude/skills`, `agents`, `commands`, `output-styles`, `design-references`, `hooks` e `mods` são junctions (Windows) ou symlinks para `~/.claude/claude_memory_sharing/<pasta>`. Nunca os apague nem os troque por pastas reais. Skill, agente ou comando novo deve ser criado dentro de `claude_memory_sharing`.
4. **Remoções se propagam.** Apagar um arquivo da pasta do Claude apaga também nos outros PCs (lá ele vai para `.sync_trash/`). Antes de apagar algo sincronizado a pedido do usuário, avise disso. Nunca use `--allow-delete` sem o usuário confirmar que a remoção grande foi de propósito: a trava existe para junction quebrada não apagar tudo nos outros PCs.
5. **Configuração vinda de outro PC.** MCPs, plugins e hooks podem apontar para programas que ainda não existem neste PC. **Não remova a config**, porque a remoção se propagaria para o PC que a usa. Instale o que falta ou pergunte.
6. **Pergunte antes** de qualquer coisa que exija login, chave nova, algo pago, deploy, push, `TRUNCATE`, troca de `SYNC_KEY`, ou que seja ambígua. Instale sozinho só o que for claramente seguro (pacote oficial, mesmo comando usado no outro PC).

## Tarefas

### Escolher o modo
Se o usuário não disse, pergunte. Ofereça o **modo Git** para quem quer começar rápido e sem custo, e o **server** para quem já o tem. Nos dois, recomende a `SYNC_KEY`. No modo Git, confirme que o repositório de dados é **privado**.

### Instalar no modo Git
Siga `git-backend.md`. Antes da primeira rodada, confira o acesso com `git ls-remote <url>`: o sync roda em segundo plano e não consegue pedir senha.

### Instalar no primeiro PC (modo server)
Siga `setup.md` → "Primeiro PC". Resumo:
1. Subir o server (README do server).
2. Clonar o cliente.
3. `keygen`.
4. `synckey`.
5. Criar o `.env`.
6. Backup e `python src/main.py`.
7. Autostart (`install_autostart.ps1` ou `install_autostart.sh`).

O usuário precisa colar a chave pública na Railway: peça a ele.

### Adicionar este PC a um sync que já existe
Siga `setup.md` → "PCs seguintes", ou `git-backend.md` no modo Git. Pare e peça ao usuário:
- a `SYNC_KEY` dos outros PCs (ele mesmo cola no `.env`);
- no modo server, para adicionar a chave pública em `CLIENT_PUBLIC_KEYS` e informar a `SERVER_URL`.

Depois da primeira rodada, faça a **verificação de pendências** abaixo.

### Verificação de pendências (depois de cada sync que trouxe algo)
1. **MCPs.** Para cada servidor em `mcpServers` do `~/.claude.json`:
   - `command` com caminho absoluto que não existe: descubra o que é (README, nome do pacote, plugin correspondente) e instale.
   - `npx`, `uvx`, `node` ou `python`: confira se o runtime existe.
   - http/sse com login: avise o usuário para autenticar.
   - No fim, rode `claude mcp list` e mostre o resultado.
2. **Plugins.** Compare `enabledPlugins` do `~/.claude/settings.json` com `~/.claude/plugins`. Para os que faltam, use `~/.claude/claude_memory_sharing/plugins-install.txt`.
3. **Hooks.** Comando que aponta para arquivo ou programa inexistente: instale ou avise.
4. **Skills com `package.json`.** Se a pasta não tem `node_modules`, rode `npm ci` nela. O `sync.log` lista essas skills com `aviso: rode 'npm ci' em ...`.
5. Se instalou algo, rode `python src/main.py` de novo para os outros PCs receberem.

### Recuperar algo
- **Arquivo removido por outro PC:** está em `.sync_trash/<data>/<caminho>` na raiz do projeto. Copie de volta para `~/.claude/claude_memory_sharing/<caminho>`.
- **Versão perdida num conflito:** está em `.sync_conflicts/<data>/<caminho>`. Mostre a diferença ao usuário e aplique o que ele quiser manter.
- **`settings.json`, `~/.claude.json` ou `CLAUDE.md` estragado:** restaure do `.bak-sync` ao lado.

### Diagnosticar
1. Leia as últimas linhas de `sync.log`, na raiz do projeto do cliente.
2. Procure a mensagem em `troubleshooting.md` (ou em `git-backend.md`, para erros do Git).
3. Para testar sem enviar nada: `python src/main.py --no-upload`. Para só preparar a `NEW_MEMORY`: `python src/main.py --offline`.

### Atualizar o código
1. `git pull` no cliente de **cada** PC. No modo server, faça também o deploy do server.
2. Reinicie o autostart: `restart_sync.bat` (Windows) ou `./install_autostart.sh --restart`.

Cliente e server com protocolos diferentes geram `HTTP 415`. Nada é corrompido, mas o sync para até os dois lados estarem na mesma versão.
