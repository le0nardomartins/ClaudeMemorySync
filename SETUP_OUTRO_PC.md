1. Abra o Claude Code no outro PC, com a pasta do projeto aberta, e cole o prompt abaixo.

```
Quero que este PC participe do meu sistema de compartilhamento de memória do Claude
entre os meus PCs. Todos os PCs são meus. Faça na ordem, me mostrando o resultado
de cada etapa, e pare para me perguntar sempre que eu precisar fazer algo manual.

Projeto: %USERPROFILE%\Documentos\interconnection-claude-memory
(cliente Python em src/; o server Node já está rodando na Railway)

ETAPA 1 - Pré-requisitos
- Verifique se Python 3 e Node estão instalados. Se faltar algo, me diga o que instalar.
- Rode: pip install -r requirements.txt   (na raiz do projeto)

ETAPA 2 - Reorganizar ~/.claude (igual ao meu outro PC)
- Crie %USERPROFILE%\.claude\claude_memory_sharing\
- Para cada pasta: skills, agents, commands, output-styles, design-references, hooks, mods
  * se ~/.claude/<pasta> existir como pasta real: mova o conteúdo para
    claude_memory_sharing/<pasta> (sem apagar nada; se houver conflito de nome,
    me pergunte)
  * se não existir: crie claude_memory_sharing/<pasta> vazia
  * crie a JUNCTION ~/.claude/<pasta> -> ~/.claude/claude_memory_sharing/<pasta>
    (PowerShell: New-Item -ItemType Junction). Confira se cada junction resolve.
- skills/synced (se existir) é do app Claude Desktop: pode ficar dentro de skills,
  mas não mexa no conteúdo dela.
- Faça backup de ~/.claude/settings.json, ~/.claude.json e ~/.claude/CLAUDE.md com
  o sufixo .bak-<data> antes de qualquer outra mudança.

ETAPA 3 - Primeira sincronização
- Rode: python src/main.py
  Ele gera os templates locais, envia este PC para o server (que junta com o que já
  existe), baixa a versão mais nova e aplica em ~/.claude/claude_memory_sharing,
  ~/.claude/settings.json e nos mcpServers do ~/.claude.json, sem substituir nada.
- Se der 401, a chave pública não está na Railway ou o redeploy não terminou.

ETAPA 4 - Instalar o que veio do outro PC (MCPs, plugins, hooks)
REGRA: se existe a config de um MCP, plugin ou hook que não existe neste PC, ele
veio do outro PC e você deve BAIXAR/INSTALAR. Nunca apague a config por isso.
- MCPs: para cada servidor em mcpServers do ~/.claude.json:
  * "command" com caminho absoluto inexistente (ex.: ~/.local/bin/headroom.EXE):
    descubra o que é e instale (pelo plugin correspondente, pip/uv, instalador oficial).
  * "command" npx/uvx/node/python: confira se o runtime existe; instale se faltar.
  * MCP http/sse com login (ex.: Figma): me avise para eu autenticar.
  * Ao final, confira se cada MCP conecta (claude mcp list) e me mostre o resultado.
- Plugins: compare enabledPlugins do settings.json com os plugins instalados e instale
  os que faltam usando ~/.claude/claude_memory_sharing/plugins-install.txt
  (tente "claude plugin marketplace add" / "claude plugin install" pelo terminal;
  se não funcionar, me passe os comandos /plugin para eu colar).
- Hooks: comando que aponta para programa/arquivo inexistente (ex.: ~/.pixel-agents):
  instale o que falta ou me diga o que é.
- Instale sozinho o que for claramente seguro (pacote oficial, mesmo comando do outro
  PC). Se precisar de login, chave nova, algo pago ou ambíguo, me pergunte.
- Se instalou algo, rode python src/main.py de novo.

O CLAUDE.md sincronizado tem a seção "CONEXÃO ENTRE PCs": a partir daqui, todo
Claude deste PC faz essa verificação sozinho no início das sessões e após cada sync.

ETAPA 5 - Conferência final
- Liste as junctions de ~/.claude e para onde apontam.
- Conte as skills e agentes disponíveis.
```
