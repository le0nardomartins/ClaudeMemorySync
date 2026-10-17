# Installation (server mode)

Prefer not to run a server? Use [git-backend.md](git-backend.md).

- [Requirements](#requirements)
- [First machine](#first-machine)
- [Next machines](#next-machines)
- [After installing](#after-installing)
- [Updating](#updating)
- [Uninstalling](#uninstalling)

Commands use `.venv/bin/python` (macOS/Linux). On Windows, use `.venv/Scripts/python`.

## Requirements

| Item | How to check | Notes |
|---|---|---|
| Python 3.10+ | `python --version` | On Windows, tick "Add to PATH" in the installer. The scheduled task uses `pythonw.exe`. |
| Git | `git --version` | To clone and update. |
| Node.js | `node --version` | Optional on the client. Only needed for skills that ship a `package.json` (`npm ci`). |
| Claude Code | `claude --version` | Run it at least once so `~/.claude` exists. |
| Running server | `https://...` URL | See the [server README](https://github.com/le0nardomartins/ClaudeMemorySync-Server). |

## First machine

### 1. Deploy the server

Follow the server README until you have its public URL (e.g. `https://your-app.up.railway.app`). Leave `CLIENT_PUBLIC_KEYS` for step 3.

### 2. Clone the client

```bash
git clone https://github.com/le0nardomartins/ClaudeMemorySync.git
cd ClaudeMemorySync
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3. Generate the machine's keys

```bash
.venv/bin/python src/components/remote_client.py keygen pc-desktop-a8f3
```

```
# .env deste PC (privado, não compartilhe):
CLIENT_ID=pc-desktop-a8f3
CLIENT_PRIVATE_KEY=...

# Railway -> CLIENT_PUBLIC_KEYS (pode ser público):
pc-desktop-a8f3:...
```

- The first two lines go into `.env`.
- The last line goes into the `CLIENT_PUBLIC_KEYS` variable of the Railway service. With several machines, separate them with commas: `pc-a:xxx,pc-b:yyy`. Railway restarts the server when you save.

### 4. Generate the encryption key (once)

```bash
.venv/bin/python src/components/remote_client.py synckey
```

The `SYNC_KEY=...` line encrypts all content before it leaves the machine. It is the **same** on every machine: generate it once, keep it in a password manager and copy it into each machine's `.env`. Without it, nobody (not even the server) can read anything.

### 5. Create `.env`

In the project root:

```ini
SERVER_URL=https://your-app.up.railway.app
CLIENT_ID=pc-desktop-a8f3
CLIENT_PRIVATE_KEY=...
SYNC_KEY=...
```

`.env` is already in `.gitignore`.

### 6. Back up and run the first round

macOS / Linux:

```bash
d=$(date +%Y%m%d)
cp ~/.claude/settings.json ~/.claude/settings.json.bak-$d
cp ~/.claude.json ~/.claude.json.bak-$d
cp ~/.claude/CLAUDE.md ~/.claude/CLAUDE.md.bak-$d 2>/dev/null
.venv/bin/python src/main.py
```

Windows (PowerShell):

```powershell
$d = Get-Date -Format yyyyMMdd
Copy-Item $HOME\.claude\settings.json "$HOME\.claude\settings.json.bak-$d"
Copy-Item $HOME\.claude.json "$HOME\.claude.json.bak-$d"
Copy-Item $HOME\.claude\CLAUDE.md "$HOME\.claude\CLAUDE.md.bak-$d" -ErrorAction SilentlyContinue
.venv\Scripts\python src\main.py
```

What happens on the first round:

1. `~/.claude/claude_memory_sharing/` is created.
2. For each portable folder (`skills`, `agents`, `commands`, `output-styles`, `design-references`, `hooks`, `mods`):
   - if `~/.claude/<folder>` is a real folder, its content moves into the sharing folder;
   - a link takes its place (junction on Windows, symlink on macOS/Linux).

   Claude Code keeps finding everything where it was.
3. `settings.template.json`, `mcp.template.json` and `plugins-install.txt` are generated, with paths replaced by `{{HOME}}` tokens.
4. Everything is mirrored into `NEW_MEMORY/`, encrypted and uploaded. The result becomes the base in `SHARING_MEMORY/`.

Expected output (shortened, log messages are in Portuguese):

```
[10:00:02] sync: atualizado skills/, ~/.claude/skills -> sharing, settings.template.json, ...
[10:00:05] NEW_MEMORY: 13 itens prontos para envio
[10:00:09] enviar: versão 1 (578 arquivos), 6.20 MB enviados
```

### 7. Run automatically

| OS | Command | What it installs |
|---|---|---|
| Windows | `powershell -ExecutionPolicy Bypass -File install_autostart.ps1` | Scheduled task `ClaudeMemorySync` (or double-click `install_autostart.bat`) |
| macOS | `./install_autostart.sh` | LaunchAgent `com.claude-memory-sync` in `~/Library/LaunchAgents` |
| Linux | `./install_autostart.sh` | systemd user service `claude-memory-sync`. Without systemd, an `@reboot` crontab line. |

In every case the sync starts at login, runs every 15 minutes, comes back if it crashes and logs to `sync.log`.

To change the interval, use `-Interval 600` or `--interval 600` (seconds). Below about 10 minutes, the Railway server never gets to sleep and costs go up.

On Linux with systemd, to keep it running without an open session: `loginctl enable-linger $USER`.

## Next machines

Repeat steps 2, 3, 5, 6 and 7 with a **different machine name** and the **same** `SYNC_KEY`, and add the new public key to `CLIENT_PUBLIC_KEYS`.

On its first round, the new machine has no base yet, so everything is treated as "added on both sides":
- what only it has is uploaded;
- what only the backend has arrives here;
- files that differ are merged line by line, with a conflict only where the same block differs.

Nothing is deleted on that first round.

> **Shortcut:** with the `claude-memory-sync` skill installed, ask Claude on the new machine: *"set up this machine in my Claude sync"*. It follows these steps, stops when you need to act (Railway, URL, `SYNC_KEY`) and runs the pending-items check at the end.

## After installing

The configuration arrives, but the programs don't. On each new machine, check:

| What | How to check | What to do |
|---|---|---|
| MCP servers | `claude mcp list` | `command` with a missing absolute path: install the program. MCP with login (OAuth): authenticate on this machine. |
| Plugins | Compare `enabledPlugins` in `settings.json` with `~/.claude/plugins` | Use the commands in `~/.claude/claude_memory_sharing/plugins-install.txt`. |
| Hooks | Look at the `command` entries in `settings.json` → `hooks` | Install the missing script or program. |
| Node skills | `sync.log` shows `aviso: rode 'npm ci' em ...` | Run `npm ci` in that folder. |

After installing something, run `python src/main.py` again so the other machines receive it.

## Updating

Client and server speak the same protocol, so update both.

```bash
git pull                              # on every machine
./install_autostart.sh --restart      # macOS / Linux
.\restart_sync.bat                    # Windows
```

The server updates when you push to its repository (Railway deploys it).

**Coming from the version with server-side merge (before the 3-way merge and encryption):**
- the new server drops the old tables on first start;
- each machine uses its existing `SHARING_MEMORY` as the base;
- the first machine to run uploads everything, and the others receive and merge.

Update the server and every machine on the same day: a machine still on the old client gets `HTTP 415` and won't sync until it's updated.

## Uninstalling

| OS | Command |
|---|---|
| Windows | `powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -Uninstall` |
| macOS / Linux | `./install_autostart.sh --uninstall` |

This only stops the sync. The links and `claude_memory_sharing` keep working as Claude's folder. To go back to the original layout on macOS/Linux, for each folder:

```bash
rm ~/.claude/<folder> && mv ~/.claude/claude_memory_sharing/<folder> ~/.claude/<folder>
```

On Windows:

```powershell
foreach ($d in 'skills','agents','commands','output-styles','design-references','hooks','mods') {
  $link = "$HOME\.claude\$d"
  if ((Get-Item $link).LinkType -eq 'Junction') {
    (Get-Item $link).Delete()
    Move-Item "$HOME\.claude\claude_memory_sharing\$d" $link
  }
}
```

To remove a machine from the sync, delete its line from `CLIENT_PUBLIC_KEYS`.
