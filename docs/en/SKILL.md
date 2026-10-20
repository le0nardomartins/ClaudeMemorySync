---
name: claude-memory-sync
description: Installs, configures and troubleshoots Claude Memory Sync, which keeps Claude Code skills, agents, commands, hooks, CLAUDE.md, settings, plugins and MCP servers identical across several machines (Windows, macOS, Linux), with a 3-way merge, deletion propagation and client-side encryption, through your own server or a private Git repository. Use when the user asks to sync Claude between computers, add a new machine to the sync, generate or rotate the SYNC_KEY, recover a deleted file or a version lost in a conflict, check MCP/plugin/hook installs that came from another machine, deploy the server on Railway, use the Git mode, or when sync.log shows an error (401, 415, 429, SYNC_KEY, --allow-delete, conflict, npm ci).
---

# Claude Memory Sync

Project:
- **Client** (Python, one per machine): https://github.com/le0nardomartins/ClaudeMemorySync
- **Backend**, one of two modes chosen in the client's `.env`:
  - **Server** (default, `SERVER_URL`): https://github.com/le0nardomartins/ClaudeMemorySync-Server (Node + Postgres on Railway)
  - **Private Git repository** (`SYNC_BACKEND=git` + `GIT_REPO_URL`): no server. See `git-backend.md`.
- **`SYNC_KEY`** (recommended, the same on every machine): encrypts everything on the machine. The backend can't even see file names.

Read on demand, in this folder:
- `setup.md`: installing from scratch, first machine and next machines (server mode), autostart on all 3 OSes
- `git-backend.md`: installing and running the serverless mode
- `architecture.md`: the round, 3-way merge, deletions, encryption, protocol
- `troubleshooting.md`: log messages, errors and recovery

The client's log messages are in Portuguese. `troubleshooting.md` lists each one with its meaning.

## Rules that always apply

1. **Secrets.** `.env` (`CLIENT_PRIVATE_KEY`, `SYNC_KEY`), `NEW_MEMORY/`, `SHARING_MEMORY/`, `.sync_conflicts/`, `.sync_trash/`, `settings.template.json` and `mcp.template.json` contain keys. Never show key or token values in chat and never send them anywhere outside the user's machines. The `SYNC_KEY` is generated once and copied by the user to each machine: guide them, but don't carry it yourself.
2. **Back up first.** Copy `~/.claude/settings.json`, `~/.claude.json` and `~/.claude/CLAUDE.md` with a `.bak-<date>` suffix before a machine's first sync.
3. **Links.** After installation, `~/.claude/skills`, `agents`, `commands`, `output-styles`, `design-references`, `hooks` and `mods` are junctions (Windows) or symlinks to `~/.claude/claude_memory_sharing/<folder>`. Never delete them or replace them with real folders. Create new skills, agents and commands inside `claude_memory_sharing`.
4. **Deletions propagate.** Deleting a file from Claude's folder deletes it on the other machines too (there it goes to `.sync_trash/`). Before deleting something synced at the user's request, warn them. Never use `--allow-delete` unless the user confirms the large deletion was intentional: the guard exists so a broken junction can't wipe the other machines.
5. **Config from another machine.** MCP servers, plugins and hooks may point to programs that don't exist on this machine yet. **Don't remove the config**: the deletion would propagate to the machine that uses it. Install what's missing or ask.
6. **Ask first** for anything that needs a login, a new key, something paid, a deploy, a push, a `TRUNCATE`, a `SYNC_KEY` rotation, or that is ambiguous. Only install on your own what is clearly safe (official package, same command used on the other machine).

## Tasks

### Choose the mode
If the user hasn't said, ask. Offer the **Git mode** to someone who wants to start fast and for free, and the **server** to someone who already has one. In both, recommend the `SYNC_KEY`. In Git mode, confirm the data repository is **private**.

### Install in Git mode
Follow `git-backend.md`. Before the first round, check access with `git ls-remote <url>`: the sync runs in the background and can't prompt for a password.

### Install on the first machine (server mode)
Follow `setup.md` → "First machine". In short:
1. Deploy the server (server README).
2. Clone the client.
3. `keygen`.
4. `synckey`.
5. Create `.env`.
6. Back up and run `python src/main.py`.
7. Autostart (`install_autostart.ps1` or `install_autostart.sh`).

The user has to paste the public key into Railway: ask them to.

### Add this machine to an existing sync
Follow `setup.md` → "Next machines", or `git-backend.md` in Git mode. Stop and ask the user:
- for the other machines' `SYNC_KEY` (they paste it into `.env` themselves);
- in server mode, to add the public key to `CLIENT_PUBLIC_KEYS` and for the `SERVER_URL`.

After the first round, run the **pending-items check** below.

### Pending-items check (after every sync that brought something)
1. **MCP servers.** For each server in `mcpServers` of `~/.claude.json`:
   - `command` with an absolute path that doesn't exist: find out what it is (README, package name, matching plugin) and install it.
   - `npx`, `uvx`, `node` or `python`: check that the runtime exists.
   - http/sse with login: tell the user to authenticate.
   - At the end, run `claude mcp list` and show the result.
2. **Plugins.** Compare `enabledPlugins` in `~/.claude/settings.json` with `~/.claude/plugins`. For missing ones, use `~/.claude/claude_memory_sharing/plugins-install.txt`.
3. **Hooks.** A command pointing to a missing file or program: install it or tell the user.
4. **Skills with `package.json`.** If the folder has no `node_modules`, run `npm ci` in it. `sync.log` lists them as `aviso: rode 'npm ci' em ...`.
5. If you installed something, run `python src/main.py` again so the other machines receive it.

### Recover something
- **File deleted by another machine:** it's in `.sync_trash/<date>/<path>` in the project root. Copy it back to `~/.claude/claude_memory_sharing/<path>`.
- **Version lost in a conflict, or an edit replaced by an old version:** the discarded version is in `.sync_conflicts/<date>/<path>` on the machine that discarded it (in a conflict the newest file by modification time always wins). Show the user the difference and apply what they want to keep. If Claude Code made the edit, its content is also in the session transcripts under `~/.claude/projects/`.
- **Broken `settings.json`, `~/.claude.json` or `CLAUDE.md`:** restore from the `.bak-sync` next to it.

### Troubleshoot
1. Read the last lines of `sync.log` in the client's project root.
2. Look the message up in `troubleshooting.md` (or `git-backend.md` for Git errors).
3. To test without sending anything: `python src/main.py --no-upload`. To only prepare `NEW_MEMORY`: `python src/main.py --offline`.

### Update the code
1. `git pull` on the client on **every** machine. In server mode, also deploy the server.
2. Restart the autostart: `./install_autostart.sh --restart` or `restart_sync.bat` (Windows).

A client and a server on different protocols produce `HTTP 415`. Nothing gets corrupted, but syncing stops until both sides match.
