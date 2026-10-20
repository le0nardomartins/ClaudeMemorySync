# Claude Memory Sync

[Português](README.md) · **English**

Keeps your **Claude Code** setup identical on every computer you use: skills, agents, slash commands, hooks, output styles, the global `CLAUDE.md`, `settings.json`, enabled plugins and MCP servers.

Work on any machine as usual. Every 15 minutes each machine sends what changed and receives what the others changed. Edits, additions and **deletions** propagate, using a 3-way merge the way Git does.

> **This repository is the client** (Python, runs on each machine). It syncs through one of two backends:
> - **Your own server** (default): [ClaudeMemorySync-Server](https://github.com/le0nardomartins/ClaudeMemorySync-Server), Node + Postgres, made for Railway.
> - **A private Git repository**, no server at all: all you need is an empty repo on GitHub, GitLab or similar.

> [!WARNING]
> What gets synced includes the **API keys** in `settings.json` and in your MCP config. Set a `SYNC_KEY` so everything is **encrypted on your machine** before it leaves (see [Security](#security)). Never publish `NEW_MEMORY/`, `SHARING_MEMORY/` or `.env`.

---

## Contents

- [Why](#why)
- [How it works](#how-it-works)
- [Two backends: server or Git repository](#two-backends-server-or-git-repository)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Adding another machine](#adding-another-machine)
- [What is synced](#what-is-synced)
- [How the merge works](#how-the-merge-works)
- [Commands](#commands)
- [Security](#security)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)
- [Full docs and Claude skill](#full-docs-and-claude-skill)

---

## Why

Claude Code keeps everything in `~/.claude` and `~/.claude.json`, and each computer has its own copy. Wrote a skill on your laptop? It doesn't exist on your desktop. Set up an MCP server at work? You have to redo it at home.

Copying the whole folder doesn't work either. It contains absolute paths (`C:\Users\jane\...`), session data, logins and machine-specific files. This project syncs **only the portable parts**, replaces user paths with neutral tokens and **merges** changes from several machines instead of letting one overwrite the other.

## How it works

```
 Each machine (every 15 min)
 ───────────────────────────
 1. PREPARE   ~/.claude/claude_memory_sharing → NEW_MEMORY/      (LOCAL state, portable paths)

 2. FETCH     backend → manifest of the newest version           (REMOTE)

 3. MERGE     BASE (SHARING_MEMORY/, last sync) × LOCAL × REMOTE
              - changed on one side only  → taken (deletions included)
              - changed on both sides     → merged by line / JSON key
              - real conflict             → the NEWEST file wins; the other goes to .sync_conflicts/
              - stale copy                → never overwrites a newer version

 4. SEND      encrypted result + "I based this on version N"
              backend already at N+1?     → fetch again, redo the merge (step 2)

 5. APPLY     result → ~/.claude/claude_memory_sharing, settings.json, ~/.claude.json, CLAUDE.md
              deleted file → .sync_trash/ (never gone for good)
```

- **What travels:** a **manifest** (path → hash and size of each file) plus only the content the backend doesn't have yet. Editing one line of a skill sends a few KB.
- **The backend is just storage.** It keeps versions and sealed content. Each machine does the merge, so the backend never needs to read anything, and with `SYNC_KEY` it can't.

| Folder (in the project root, except the first) | Role |
|---|---|
| `~/.claude/claude_memory_sharing/` | The real memory. `~/.claude/skills`, `agents`, `commands`, `hooks`, `output-styles`, `design-references` and `mods` become *junctions* (folder links) pointing here. |
| `NEW_MEMORY/` | This machine's **local** state, in portable form. |
| `SHARING_MEMORY/` | **Base**: the version this machine and the backend agreed on at the last sync. It's what makes it possible to tell who changed what. |
| `.sync_conflicts/` | Versions that lost a conflict, by date. |
| `.sync_trash/` | Files deleted by another machine, by date. |

Details in [docs/en/architecture.md](docs/en/architecture.md).

## Two backends: server or Git repository

The flow above is the same for both. Only **where versions are stored** changes.

| | Server (default) | Git repository (no server) |
|---|---|---|
| Where | Node + Postgres on Railway (or any host) | A **private** repo on GitHub, GitLab, Gitea... |
| Authentication | One Ed25519 key per machine | Your existing Git credentials (SSH or HTTPS) |
| Concurrency | The server rejects uploads based on a stale version (409) | Git rejects the push |
| Cost | Railway plan | Free |
| Setup effort | Deploy + database + variables | Create an empty repo |
| History | Last 3 versions | Full Git history |

To use the Git backend, put this in `.env`:

```ini
SYNC_BACKEND=git
GIT_REPO_URL=git@github.com:you/claude-memory-data.git
```

Step by step in [docs/en/git-backend.md](docs/en/git-backend.md).

## Requirements

| | |
|---|---|
| OS | Windows 10/11, macOS or Linux. Autostart uses Task Scheduler (Windows), launchd (macOS) or systemd user services / cron (Linux). |
| Python | 3.10 or newer, with `pip install -r requirements.txt` (just `cryptography`). |
| Backend | A running [server](https://github.com/le0nardomartins/ClaudeMemorySync-Server) **or** an empty private Git repository, with `git` installed on each machine. |
| Claude Code | Installed on each machine, with `~/.claude` already created. |

## Quick start

> To use a Git repository instead of the server, follow [docs/en/git-backend.md](docs/en/git-backend.md): steps 1, 3 and 5 change, the rest is the same.

**1. Deploy the server.** Follow the [server README](https://github.com/le0nardomartins/ClaudeMemorySync-Server). You'll end up with a URL like `https://your-app.up.railway.app`.

**2. Clone this repository and install the dependency:**

```bash
git clone https://github.com/le0nardomartins/ClaudeMemorySync.git
cd ClaudeMemorySync
python -m venv .venv
.venv/bin/pip install -r requirements.txt     # Windows: .venv/Scripts/pip
```

**3. Generate this machine's key pair:**

```bash
.venv/bin/python src/components/remote_client.py keygen pc-laptop-x7k2
```

The output has two parts:
- `CLIENT_ID` and `CLIENT_PRIVATE_KEY` go into this machine's `.env`. They are private.
- `pc-laptop-x7k2:<base64>` goes into the server's `CLIENT_PUBLIC_KEYS` variable. Separate machines with commas. This line can be public.

**4. Generate the encryption key, once for all machines:**

```bash
.venv/bin/python src/components/remote_client.py synckey
```

The `SYNC_KEY=...` line goes, **identical**, into the `.env` of every machine you own. Keep it in a password manager: without it, nobody (including you) can read what's on the backend.

**5. Create `.env`** in the project root:

```ini
SERVER_URL=https://your-app.up.railway.app
CLIENT_ID=pc-laptop-x7k2
CLIENT_PRIVATE_KEY=...
SYNC_KEY=...
```

**6. Run once and check the output:**

```bash
.venv/bin/python src/main.py
```

On the first run the sync:
- creates `~/.claude/claude_memory_sharing`;
- moves `skills`, `agents` and the other portable folders there and puts links in their place;
- generates the settings and MCP templates and uploads everything, encrypted.

**7. Run it automatically:**

| OS | Command |
|---|---|
| Windows | `powershell -ExecutionPolicy Bypass -File install_autostart.ps1` |
| macOS / Linux | `./install_autostart.sh` |

The log goes to `sync.log`. To remove it, use `-Uninstall` (Windows) or `--uninstall` (macOS/Linux).

## Adding another machine

Repeat steps 2, 3, 5, 6 and 7 on the other computer with a different machine name and the **same** `SYNC_KEY`, and add its public key to `CLIENT_PUBLIC_KEYS` on the server.

An easier way: open Claude Code on the new machine and ask it to use this project's skill (see below). It walks through installation and backups, and checks the MCP servers and plugins that came from the first machine. The manual steps are in [docs/en/setup.md](docs/en/setup.md).

## What is synced

| Synced | Not synced |
|---|---|
| `skills/`, `agents/`, `commands/`, `hooks/`, `output-styles/`, `design-references/`, `mods/` | `node_modules/`: reinstall with `npm ci` (the log tells you which skills need it) |
| Global `~/.claude/CLAUDE.md` | `skills/synced/`: managed by the Claude Desktop app on each machine |
| `~/.claude/settings.json`: permissions, hooks, env, enabled plugins | `.git/` and `__pycache__/` |
| `mcpServers` in `~/.claude.json` (global ones only) | Claude login (`.credentials.json`), history and sessions |
| Commands to reinstall plugins (`plugins-install.txt`) | Project MCP servers (`.mcp.json`) |

The sync carries MCP and plugin **configuration**, not the programs. If an MCP server points to an executable that only exists on the other machine, it has to be installed here. The project skill checks this for you.

## How the merge works

It's a **3-way** merge: each side is compared with the **base** (the version from the last sync), so it's possible to tell who changed what.

| Situation | Result |
|---|---|
| Only one machine changed a block | The change is applied. Editing a line **replaces** it (no duplicates). |
| Only one machine removed a line, key or file | The removal propagates. |
| Both changed different blocks of the same file | Both changes are applied. |
| Both added lines at the same spot (e.g. a new section at the end) | Both blocks are kept whole, one after the other. |
| Both changed the same block differently | **Conflict**: the block from the **newest** file (modification time) wins; the other version is saved in `.sync_conflicts/` and logged. |
| One machine "changed" a file to content **older** than the other's (stale copy, lost base) | The newest version stays. An old copy never overwrites an edit. |
| The file exists on both machines, different, with no base (new machine) | The newest whole file wins; the other goes to `.sync_conflicts/`. |
| You edit a file while the sync is running | Your edit is kept; the sync doesn't write over it and merges it next round. |
| One machine deleted a file and the other edited it | The edit wins: the file stays. |

Details by file type:
- **JSON** is merged per key, and lists per item. That covers `settings.json` (permissions, hooks, env) and the `mcpServers` in `~/.claude.json`, which receive only the difference, with a `.bak-sync` backup.
- The global **`CLAUDE.md`** goes through the same text merge, also with a `.bak-sync` backup.
- A **binary** file changed on both sides is a whole-file conflict.
- **"Newest" means the file's modification time**, which travels with it. A received file keeps the time of the original edit. That's why machine clocks must be right (automatic time sync on).

**Mass-deletion guard:** if more than 20% of the files (and more than 10) disappear from a machine at once, for example because of a broken junction, the sync stops and asks for confirmation (`--allow-delete`) instead of deleting everything on the other machines. A file deleted by another machine goes to `.sync_trash/` and is never gone for good.

## Commands

| Command | What it does |
|---|---|
| `python src/main.py` | One full round: prepare → fetch → merge → send → apply. |
| `python src/main.py --loop` | Runs forever, every 15 minutes (`--interval <seconds>` to change). |
| `python src/main.py --no-upload` | Only receives and applies. Local changes go out on the next normal round. |
| `python src/main.py --offline` | Only prepares `NEW_MEMORY`, no network. |
| `python src/main.py --allow-delete` | Allows an intentional large deletion. |
| `python src/components/remote_client.py keygen <pc>` | Generates a machine's Ed25519 keys (server mode). |
| `python src/components/remote_client.py synckey` | Generates the `SYNC_KEY` (once, for all machines). |
| `install_autostart.ps1 [-Interval 900] [-Uninstall]` | Autostart on Windows (scheduled task). |
| `install_autostart.sh [--interval 900] [--restart] [--uninstall]` | Autostart on macOS (launchd) and Linux (systemd or cron). |
| `restart_sync.bat` | Restarts the Windows task (use after `git pull`). |

Only one sync runs at a time on each machine (`.sync.lock`). A second run exits on its own.

## Security

- **Client-side encryption (`SYNC_KEY`).** Content and manifest are encrypted with AES-256-GCM before leaving the machine. Each file's id on the backend is an HMAC, which reveals nothing about the content and can't be used to confirm a known file is there. Not even file names are visible. A leaked database or repository doesn't expose your API keys.
- **No shared password on the server.** Each machine has an Ed25519 key pair. The private key stays in that machine's `.env`, and the server only stores public keys.
- **Every request is signed.** The signature covers method, path, time and body hash. The server rejects replayed signatures, altered bodies and requests older than 5 minutes.
- **HTTPS only.** The client refuses `http://` except for `localhost`.
- **Integrity.** All received content is verified (GCM and sha256), and paths like `../` are rejected.
- **Revoking a machine:** remove its line from `CLIENT_PUBLIC_KEYS` (server) or its access to the repository (Git). If the machine was compromised, also rotate the `SYNC_KEY` on every machine ([how](docs/en/troubleshooting.md#rotating-the-sync_key)).

Without `SYNC_KEY`, the sync still works, but content is only compressed, not secret.

## Limitations

- **Short history (server).** The server keeps only the last 3 versions. It isn't a backup, but `.sync_trash/` and `.sync_conflicts/` keep whatever left each machine.
- **Programs don't travel.** MCP executables, plugins and logins (e.g. Figma OAuth) must be installed or authenticated on each machine.
- **Clocks.** Conflicts are decided by modification time: a machine whose clock is far ahead or behind makes its edits look newer or older than they are. The discarded version always stays in `.sync_conflicts/`.
- **Git mode history grows forever.** Each sync is a commit; for configs that's small, but you can restart the history ([how](docs/en/git-backend.md#repository-size)).
- **`SYNC_KEY` must match on every machine.** Lost it? Generate a new one and reset the backend ([how](docs/en/troubleshooting.md#rotating-the-sync_key)): the machines upload everything again.

## Troubleshooting

Log messages are in Portuguese. They appear here exactly as logged.

| Symptom | Likely cause |
|---|---|
| `não consegui decifrar: a SYNC_KEY deste PC é diferente` | This machine's `SYNC_KEY` differs from the others. |
| `os dados estão criptografados: defina a mesma SYNC_KEY` | This machine has no `SYNC_KEY`. |
| `N arquivos sumiram deste PC ... --allow-delete` | The mass-deletion guard fired. Check the junctions; if the deletion was intentional, run with `--allow-delete`. |
| `conflito em N arquivo(s): ficou a versão mais nova` | Two machines changed the same block, or one had a stale copy. The discarded version is in `.sync_conflicts/`. |
| `editado durante o sync, mantido como está` | You edited the file while the round was running. Nothing was overwritten; the next round merges it. |
| `HTTP 401` | This machine's public key isn't in `CLIENT_PUBLIC_KEYS`, `CLIENT_ID` doesn't match, or the clock is way off (±5 min). |
| `HTTP 415` / `formato antigo` | Client and server on different versions. Update both (`git pull`) and restart the sync. |
| `git push falhou` / `git clone falhou` (Git mode) | This machine's `git` can't reach `GIT_REPO_URL`. Test with `git ls-remote <url>`. |
| `aviso: rode 'npm ci' em ...` | A skill with `package.json` arrived without `node_modules`. Run `npm ci` in that folder. |

More cases in [docs/en/troubleshooting.md](docs/en/troubleshooting.md).

## Project layout

```
src/
  main.py                          full round, --loop, lock, log
  components/
    sync_claude_templates.py       junctions/links + settings/MCP templates (paths → tokens)
    sync_engine.py                 the round: base × local × remote, upload, apply, guard, trash
    merge3.py                      3-way merge for text, JSON and binary
    crypto_codec.py                SYNC_KEY: AES-256-GCM + HMAC ids
    remote_client.py               server backend (signed HTTP), bundle, keygen, synckey
    git_backend.py                 private Git repository backend
docs/                              full docs in Portuguese (also a Claude skill)
docs/en/                           full docs in English (also a Claude skill)
install_autostart.ps1 / .bat       autostart on Windows
install_autostart.sh               autostart on macOS and Linux
restart_sync.bat                   restarts the Windows task after updating the code
requirements.txt
```

Local files that are **not** committed: `.env`, `NEW_MEMORY/`, `SHARING_MEMORY/`, `.sync_repo/`, `.sync_conflicts/`, `.sync_trash/`, `.sync.lock` and `sync.log`.

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q tests
```

The tests cover the 3-way merge (`tests/test_merge3.py`) and full rounds between several simulated machines (`tests/test_sync.py`): conflicts, stale copies, edits during a sync, deletions, templates, encryption and the Git backend. They run on GitHub Actions on Windows, macOS and Linux.

## License

[MIT](LICENSE).

## Full docs and Claude skill

The [`docs/en/`](docs/en/) folder has the detailed documentation and is also a **Claude Code skill** (`claude-memory-sync`). With it installed, Claude on any machine can install the project, set up a new machine, check pending MCP and plugin installs, and diagnose errors.

```bash
git clone --depth 1 https://github.com/le0nardomartins/ClaudeMemorySync.git /tmp/cms
cp -r /tmp/cms/docs/en ~/.claude/skills/claude-memory-sync     # Windows: Copy-Item -Recurse
```

Or just ask Claude: *"install the docs/en folder from https://github.com/le0nardomartins/ClaudeMemorySync as a skill named claude-memory-sync"*.

| Document | Contents |
|---|---|
| [docs/en/setup.md](docs/en/setup.md) | Installing from scratch, first machine and the next ones |
| [docs/en/git-backend.md](docs/en/git-backend.md) | No-server mode with a private Git repository |
| [docs/en/architecture.md](docs/en/architecture.md) | Round, 3-way merge, protocol, encryption |
| [docs/en/troubleshooting.md](docs/en/troubleshooting.md) | Errors, logs and recovery |
| [docs/en/SKILL.md](docs/en/SKILL.md) | Instructions Claude follows when using the skill |
