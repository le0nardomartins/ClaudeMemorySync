# Instalação (modo server)

Prefere não ter server? Use [git-backend.md](git-backend.md).

- [Pré-requisitos](#pré-requisitos)
- [Primeiro PC](#primeiro-pc)
- [PCs seguintes](#pcs-seguintes)
- [Depois de instalar](#depois-de-instalar)
- [Atualizar](#atualizar)
- [Desinstalar](#desinstalar)

Os comandos usam `.venv/Scripts/python`, que é o caminho no Windows. No macOS e no Linux, use `.venv/bin/python`.

## Pré-requisitos

| Item | Como conferir | Observação |
|---|---|---|
| Python 3.10+ | `python --version` | No Windows, marque "Add to PATH" no instalador. A tarefa agendada usa o `pythonw.exe`. |
| Git | `git --version` | Para clonar e atualizar. |
| Node.js | `node --version` | Opcional no cliente. Só é preciso para skills que tenham `package.json` (`npm ci`). |
| Claude Code | `claude --version` | Rode pelo menos uma vez para criar `~/.claude`. |
| Server no ar | URL `https://...` | Veja o [README do server](https://github.com/le0nardomartins/ClaudeMemorySync-Server). |

## Primeiro PC

### 1. Subir o server

Siga o README do server até ter a URL pública (ex.: `https://seu-app.up.railway.app`). Deixe a variável `CLIENT_PUBLIC_KEYS` para o passo 3.

### 2. Clonar o cliente

```bash
git clone https://github.com/le0nardomartins/ClaudeMemorySync.git
cd ClaudeMemorySync
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
```

### 3. Gerar as chaves do PC

```bash
.venv/Scripts/python src/components/remote_client.py keygen pc-desktop-a8f3
```

```
# .env deste PC (privado, não compartilhe):
CLIENT_ID=pc-desktop-a8f3
CLIENT_PRIVATE_KEY=...

# Railway -> CLIENT_PUBLIC_KEYS (pode ser público):
pc-desktop-a8f3:...
```

- As duas primeiras linhas vão para o `.env`.
- A última vai para a variável `CLIENT_PUBLIC_KEYS` do serviço na Railway. Com vários PCs, separe por vírgula: `pc-a:xxx,pc-b:yyy`. A Railway reinicia o server sozinha ao salvar.

### 4. Gerar a chave de criptografia (uma vez só)

```bash
.venv/Scripts/python src/components/remote_client.py synckey
```

A linha `SYNC_KEY=...` cifra todo o conteúdo antes de sair do PC. Ela é a **mesma** em todos os seus PCs: gere uma única vez, guarde num gerenciador de senhas e copie para o `.env` de cada PC. Quem não tem a chave (nem o server) não lê nada.

### 5. Criar o `.env`

Na raiz do projeto:

```ini
SERVER_URL=https://seu-app.up.railway.app
CLIENT_ID=pc-desktop-a8f3
CLIENT_PRIVATE_KEY=...
SYNC_KEY=...
```

O `.env` já está no `.gitignore`.

### 6. Backup e primeira rodada

Windows (PowerShell):

```powershell
$d = Get-Date -Format yyyyMMdd
Copy-Item $HOME\.claude\settings.json "$HOME\.claude\settings.json.bak-$d"
Copy-Item $HOME\.claude.json "$HOME\.claude.json.bak-$d"
Copy-Item $HOME\.claude\CLAUDE.md "$HOME\.claude\CLAUDE.md.bak-$d" -ErrorAction SilentlyContinue
.venv\Scripts\python src\main.py
```

macOS / Linux:

```bash
d=$(date +%Y%m%d)
cp ~/.claude/settings.json ~/.claude/settings.json.bak-$d
cp ~/.claude.json ~/.claude.json.bak-$d
cp ~/.claude/CLAUDE.md ~/.claude/CLAUDE.md.bak-$d 2>/dev/null
.venv/bin/python src/main.py
```

O que acontece na primeira rodada:

1. É criada `~/.claude/claude_memory_sharing/`.
2. Para cada pasta portátil (`skills`, `agents`, `commands`, `output-styles`, `design-references`, `hooks`, `mods`):
   - se `~/.claude/<pasta>` é uma pasta real, o conteúdo dela é movido para o sharing;
   - no lugar dela é criado um link (junction no Windows, symlink no macOS/Linux).

   O Claude Code continua achando tudo no mesmo lugar.
3. São gerados `settings.template.json`, `mcp.template.json` e `plugins-install.txt`, com os caminhos trocados por tokens `{{HOME}}`.
4. Tudo é espelhado em `NEW_MEMORY/`, cifrado e enviado. O resultado vira a base em `SHARING_MEMORY/`.

Saída esperada (resumida):

```
[10:00:02] sync: atualizado skills/, ~/.claude/skills -> sharing, settings.template.json, ...
[10:00:05] NEW_MEMORY: 13 itens prontos para envio
[10:00:09] enviar: versão 1 (578 arquivos), 6.20 MB enviados
```

### 7. Rodar sozinho

| Sistema | Comando | O que instala |
|---|---|---|
| Windows | `powershell -ExecutionPolicy Bypass -File install_autostart.ps1` | Tarefa agendada `ClaudeMemorySync` (também dá para usar `install_autostart.bat` com dois cliques) |
| macOS | `./install_autostart.sh` | LaunchAgent `com.claude-memory-sync` em `~/Library/LaunchAgents` |
| Linux | `./install_autostart.sh` | Serviço de usuário `claude-memory-sync` no systemd. Sem systemd, uma linha `@reboot` no crontab. |

Em todos, o sync inicia no login, roda a cada 15 minutos, volta sozinho se cair e grava o log em `sync.log`.

Para mudar o intervalo, use `-Interval 600` ou `--interval 600` (segundos). Abaixo de ~10 minutos o server da Railway não chega a dormir, e o custo sobe.

No Linux com systemd, para o sync rodar mesmo sem sessão aberta: `loginctl enable-linger $USER`.

## PCs seguintes

Repita os passos 2, 3, 5, 6 e 7 com um **nome de PC diferente** e a **mesma** `SYNC_KEY`, e acrescente a chave pública nova em `CLIENT_PUBLIC_KEYS`.

Na primeira rodada, o PC novo ainda não tem base, então tudo é tratado como "acrescentado dos dois lados":
- o que só ele tem é enviado;
- o que só o destino tem chega aqui;
- arquivo que existe nos dois com conteúdo diferente fica com a versão **mais nova** inteira, e a outra vai para `.sync_conflicts/`.

Nada é removido nessa primeira rodada.

> **Atalho:** com a skill `claude-memory-sync` instalada, peça ao Claude do PC novo: *"configure este PC no meu sync do Claude"*. Ele segue estes passos, para quando você precisa agir (Railway, URL, `SYNC_KEY`) e faz a verificação de pendências no final.

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

```bash
git pull                              # em cada PC
.\restart_sync.bat                    # Windows
./install_autostart.sh --restart      # macOS / Linux
```

O server atualiza com push no repositório dele (a Railway faz o deploy).

**Vindo da versão com merge no server (antes do merge de 3 vias e da criptografia):**
- o server novo apaga as tabelas antigas no primeiro start;
- cada PC usa a `SHARING_MEMORY` que já tem como base;
- o primeiro PC a rodar reenvia tudo, e os outros recebem e mesclam.

Atualize o server e todos os PCs no mesmo dia: um PC com o cliente antigo recebe `HTTP 415` e não sincroniza até ser atualizado.

## Desinstalar

| Sistema | Comando |
|---|---|
| Windows | `powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -Uninstall` |
| macOS / Linux | `./install_autostart.sh --uninstall` |

Isso só para o sync. Os links e a `claude_memory_sharing` continuam funcionando normalmente como a pasta do Claude. Para voltar ao layout original no Windows, troque cada junction pela pasta real:

```powershell
foreach ($d in 'skills','agents','commands','output-styles','design-references','hooks','mods') {
  $link = "$HOME\.claude\$d"
  if ((Get-Item $link).LinkType -eq 'Junction') {
    (Get-Item $link).Delete()
    Move-Item "$HOME\.claude\claude_memory_sharing\$d" $link
  }
}
```

No macOS/Linux: `rm ~/.claude/<pasta> && mv ~/.claude/claude_memory_sharing/<pasta> ~/.claude/<pasta>` para cada pasta.

Para tirar um PC do sync, apague a linha dele em `CLIENT_PUBLIC_KEYS`.
