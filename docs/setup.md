# Instalação

- [Pré-requisitos](#pré-requisitos)
- [Primeiro PC](#primeiro-pc)
- [PCs seguintes](#pcs-seguintes)
- [Depois de instalar](#depois-de-instalar)
- [Atualizar](#atualizar)
- [Desinstalar](#desinstalar)

## Pré-requisitos

| Item | Como conferir | Observação |
|---|---|---|
| Python 3.10+ | `python --version` | No Windows, marque "Add to PATH" no instalador. O `pythonw.exe` é usado pela tarefa agendada. |
| Git | `git --version` | Para clonar e atualizar. |
| Node.js | `node --version` | Opcional no cliente. Só é preciso para skills que tenham `package.json` (`npm ci`). |
| Claude Code | `claude --version` | Rode pelo menos uma vez para criar `~/.claude`. |
| Server no ar | URL `https://...` | Veja o [README do backend](https://github.com/le0nardomartins/ClaudeMemorySync-Server). |

## Primeiro PC

### 1. Subir o server

Siga o README do backend até ter a URL pública (ex.: `https://seu-app.up.railway.app`). Deixe a variável `CLIENT_PUBLIC_KEYS` para o passo 3.

### 2. Clonar o cliente

```powershell
cd $HOME\Documents
git clone https://github.com/le0nardomartins/ClaudeMemorySync.git
cd ClaudeMemorySync
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

### 3. Gerar as chaves do PC

```powershell
.venv\Scripts\python src\components\remote_client.py keygen pc-desktop-a8f3
```

```
# .env deste PC (privado, não compartilhe):
CLIENT_ID=pc-desktop-a8f3
CLIENT_PRIVATE_KEY=...

# Railway -> CLIENT_PUBLIC_KEYS (pode ser público):
pc-desktop-a8f3:...
```

- As duas primeiras linhas vão para o `.env` (próximo passo).
- A última vai para a variável `CLIENT_PUBLIC_KEYS` do serviço na Railway. Com vários PCs, separe por vírgula: `pc-a:xxx,pc-b:yyy`. A Railway reinicia o server sozinha ao salvar.

### 4. Criar o `.env`

Na raiz do projeto:

```ini
SERVER_URL=https://seu-app.up.railway.app
CLIENT_ID=pc-desktop-a8f3
CLIENT_PRIVATE_KEY=...
```

O `.env` já está no `.gitignore`.

### 5. Backup e primeira rodada

```powershell
$d = Get-Date -Format yyyyMMdd
Copy-Item $HOME\.claude\settings.json "$HOME\.claude\settings.json.bak-$d"
Copy-Item $HOME\.claude.json "$HOME\.claude.json.bak-$d"
Copy-Item $HOME\.claude\CLAUDE.md "$HOME\.claude\CLAUDE.md.bak-$d" -ErrorAction SilentlyContinue

.venv\Scripts\python src\main.py
```

O que acontece na primeira rodada:

1. É criada `~/.claude/claude_memory_sharing/`.
2. Para cada pasta portátil (`skills`, `agents`, `commands`, `output-styles`, `design-references`, `hooks`, `mods`):
   - se `~/.claude/<pasta>` é uma pasta real, o conteúdo dela é movido para o sharing;
   - no lugar dela é criada uma junction.

   O Claude Code continua achando tudo no mesmo lugar.
3. São gerados `settings.template.json`, `mcp.template.json` e `plugins-install.txt`, com os caminhos trocados por tokens `{{HOME}}`.
4. Tudo é espelhado em `NEW_MEMORY/` e enviado ao server.

Saída esperada (resumida):

```
[10:00:01] receber: nada novo no server
[10:00:02] sync: atualizado skills/, ~/.claude/skills -> sharing, settings.template.json, ...
[10:00:05] NEW_MEMORY: 13 itens prontos para envio
[10:00:09] enviar: 6.20 MB enviados -> versão 1 no server (578 arquivos)
```

### 6. Rodar sozinho

```powershell
powershell -ExecutionPolicy Bypass -File install_autostart.ps1
```

Isso cria a tarefa agendada `ClaudeMemorySync`, que inicia ao entrar no Windows, roda a cada 15 minutos e se reinicia se cair. O log vai para `sync.log`. Também dá para usar `install_autostart.bat` com dois cliques.

Para mudar o intervalo, use `-Interval 600` (em segundos). Abaixo de ~10 minutos o server da Railway não chega a dormir, e o custo sobe.

## PCs seguintes

Repita os passos 2 a 6 com um **nome de PC diferente**, e acrescente a chave pública nova em `CLIENT_PUBLIC_KEYS`.

Na primeira rodada, o PC novo:
- recebe a versão do server em `SHARING_MEMORY/`;
- mescla essa versão na pasta do Claude: skills e agentes novos entram, e `settings.json` e `~/.claude.json` são complementados com backup `.bak-sync`;
- envia o que só ele tinha.

> **Atalho:** com a skill `claude-memory-sync` instalada, peça ao Claude do PC novo: *"configure este PC no meu sync do Claude"*. Ele segue estes passos, para quando você precisa agir (Railway, URL) e faz a verificação de pendências no final.

## Depois de instalar

A configuração chega, mas os programas não. Confira em cada PC novo:

| O quê | Como conferir | O que fazer |
|---|---|---|
| MCPs | `claude mcp list` | `command` com caminho absoluto inexistente: instale o programa. MCP com login (OAuth): autentique neste PC. |
| Plugins | Compare `enabledPlugins` do `settings.json` com `~/.claude/plugins` | Use os comandos de `~/.claude/claude_memory_sharing/plugins-install.txt`. |
| Hooks | Veja os `command` em `settings.json` → `hooks` | Instale o script ou programa que faltar. |
| Skills com Node | O `sync.log` mostra `aviso: rode 'npm ci' em ...` | Rode `npm ci` na pasta indicada. |

Depois de instalar algo, rode `python src/main.py` de novo para os outros PCs receberem.

## Atualizar

Cliente e server falam o mesmo protocolo: atualize os dois.

```powershell
git pull                 # em cada PC
.\restart_sync.bat       # reinicia a tarefa com o código novo
```

O server atualiza com push no repositório dele (a Railway faz o deploy). Migrações de banco rodam sozinhas no start.

## Desinstalar

```powershell
powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -Uninstall
```

Isso só para o sync. As junctions e a `claude_memory_sharing` continuam funcionando normalmente como a pasta do Claude. Para voltar ao layout original, troque cada junction pela pasta real:

```powershell
foreach ($d in 'skills','agents','commands','output-styles','design-references','hooks','mods') {
  $link = "$HOME\.claude\$d"
  if ((Get-Item $link).LinkType -eq 'Junction') {
    (Get-Item $link).Delete()
    Move-Item "$HOME\.claude\claude_memory_sharing\$d" $link
  }
}
```

Para tirar um PC do sync, apague a linha dele em `CLIENT_PUBLIC_KEYS`.
