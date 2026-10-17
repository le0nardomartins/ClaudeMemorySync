# Claude Memory Sync documentation (client)

[Português](../README.md) · **English**

This folder has two uses:

1. **Documentation** for the client, for anyone who wants to install, understand or change the project.
2. **A Claude Code skill** (`claude-memory-sync`). `SKILL.md` teaches Claude to install the project, add a machine, check pending MCP/plugin installs and diagnose errors.

## Index

| Document | For | Contents |
|---|---|---|
| [setup.md](setup.md) | Installers | Requirements, first machine, next machines, updating and uninstalling |
| [git-backend.md](git-backend.md) | Anyone who doesn't want a server | Using a private Git repository instead of the server |
| [architecture.md](architecture.md) | Contributors | Sync round, folders, server protocol, merge, local state |
| [troubleshooting.md](troubleshooting.md) | Anyone with an error | `sync.log` messages, HTTP codes and recovery |
| [SKILL.md](SKILL.md) | Claude | Rules and playbook Claude follows when using the skill |

The server has its own `docs/en/` folder in [ClaudeMemorySync-Server](https://github.com/le0nardomartins/ClaudeMemorySync-Server/tree/main/docs/en).

## Install as a skill

The skill is just this folder, copied into Claude Code's skills as `claude-memory-sync`.

**Windows (PowerShell):**

```powershell
git clone --depth 1 https://github.com/le0nardomartins/ClaudeMemorySync.git $env:TEMP\cms
Copy-Item -Recurse $env:TEMP\cms\docs\en $HOME\.claude\skills\claude-memory-sync
Remove-Item -Recurse -Force $env:TEMP\cms
```

**macOS / Linux:**

```bash
git clone --depth 1 https://github.com/le0nardomartins/ClaudeMemorySync.git /tmp/cms
cp -r /tmp/cms/docs/en ~/.claude/skills/claude-memory-sync
rm -rf /tmp/cms
```

**Ask Claude.** In a Claude Code session:

> Install the `docs/en` folder from https://github.com/le0nardomartins/ClaudeMemorySync as a skill named `claude-memory-sync`.

Once installed, ask in plain language: *"set up this machine in my Claude sync"*, *"the sync is returning 401"*, *"check whether an MCP server from my other machine still needs installing"*.

> If the sync is already running, the skill itself is synced to your other machines, because it lives in `skills/`.

## 30-second overview

```
 Machine A ──┐                          ┌── Machine B
             │  manifest + only what    │
             ├──► changed (signed) ───► │
             │   SERVER or GIT REPO     │
             │   every content stored   │
             │   once, by sha256        │
             └──◄ only what's missing ◄─┘
```

- Each machine sends the **manifest** of its files (path → sha256) and only the content the backend doesn't have.
- The backend **merges** it with the previous version, without deleting anything, and stores the new version.
- Other machines fetch the new manifest, request only what they lack and **merge** it into their local setup.
