---
name: claude-memory-sync
description: Instala, configura e diagnostica o Claude Memory Sync, que mantém skills, agentes, comandos, hooks, CLAUDE.md, settings, plugins e MCPs do Claude Code iguais entre vários PCs por meio de um server próprio. Use quando o usuário pedir para sincronizar o Claude entre computadores, configurar um PC novo no sync, conferir pendências de MCP/plugin/hook vindas de outro PC, subir o server na Railway, ou quando o sync.log mostrar erro (401, 409, 415, 429, manifesto inválido, npm ci).
---

# Claude Memory Sync

Projeto em duas partes:
- **Cliente** (Python, um por PC): https://github.com/le0nardomartins/ClaudeMemorySync
- **Server** (Node + Postgres, na Railway): https://github.com/le0nardomartins/ClaudeMemorySync-Server

Leia sob demanda, nesta pasta:
- `setup.md`: instalação do zero, primeiro PC e PCs seguintes
- `architecture.md`: como a rodada, o protocolo e o merge funcionam
- `troubleshooting.md`: erros e como recuperar

## Regras que valem sempre

1. **Segredos.** `NEW_MEMORY/`, `SHARING_MEMORY/`, `.env`, `settings.template.json` e `mcp.template.json` contêm chaves de API. Nunca mostre valores de chave ou token no chat, nunca faça commit desses arquivos e nunca os envie para fora dos PCs do usuário.
2. **Backup antes de mexer.** Copie `~/.claude/settings.json`, `~/.claude.json` e `~/.claude/CLAUDE.md` com o sufixo `.bak-<data>` antes da primeira sincronização de um PC.
3. **Junctions.** Depois da instalação, `~/.claude/skills`, `agents`, `commands`, `output-styles`, `design-references`, `hooks` e `mods` são junctions (Windows) ou symlinks para `~/.claude/claude_memory_sharing/<pasta>`. Nunca apague nem troque essas junctions por pastas reais. Skill, agente ou comando novo deve ser criado dentro de `claude_memory_sharing`.
4. **Nada se apaga sozinho.** Apagar um arquivo não se propaga: os outros PCs e o server o devolvem no próximo sync. Quando o usuário pedir para remover algo sincronizado, avise disso e, se ele quiser remover de vez, siga `troubleshooting.md` → "Um arquivo que apaguei voltou" (isso zera o histórico do server, então confirme antes).
5. **Configuração vinda de outro PC.** MCPs, plugins e hooks podem apontar para programas que ainda não existem neste PC. Não remova a configuração (o outro PC usa): instale o que falta ou pergunte.
6. **Pergunte antes** de qualquer coisa que exija login, chave nova, algo pago, deploy, push ou que seja ambígua. Instale sozinho só o que for claramente seguro (pacote oficial, mesmo comando usado no outro PC).

## Tarefas

### Instalar no primeiro PC (o server ainda não existe)
Siga `setup.md` → "Primeiro PC". Resumo:
1. Subir o server: README do backend.
2. Clonar o cliente.
3. Gerar chaves.
4. Criar o `.env`.
5. Rodar `python src/main.py`.
6. Rodar `install_autostart.ps1`.

O usuário precisa colar a chave pública na Railway: peça a ele.

### Adicionar este PC a um sync que já existe
Siga `setup.md` → "PCs seguintes". Pare e peça ao usuário:
- para adicionar a chave pública em `CLIENT_PUBLIC_KEYS` na Railway;
- a `SERVER_URL`.

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

### Diagnosticar
1. Leia as últimas linhas de `sync.log`, na raiz do projeto do cliente.
2. Procure o erro em `troubleshooting.md`.
3. Para testar sem enviar nada: `python src/main.py --no-upload`. Para só preparar a `NEW_MEMORY`: `python src/main.py --offline`.

### Atualizar o código
1. `git pull` no cliente de **cada** PC e no server (deploy na Railway).
2. Depois, `restart_sync.bat` em cada PC.

Cliente e server com protocolos diferentes geram `HTTP 415` ou `manifesto inválido`. Nada é corrompido, mas o sync para até os dois lados estarem na mesma versão.
