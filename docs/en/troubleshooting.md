# Troubleshooting

Start with the log: `sync.log` in the project root when running through autostart, or the output of `python src/main.py` in a terminal. Log messages are in Portuguese. The tables below quote them as they appear.

## Log messages

| Message | Meaning | What to do |
|---|---|---|
| `sync: nada mudou` | Neither this machine nor the backend changed since the last sync. | Nothing. |
| `enviar: versão N (... arquivos), X enviados` | This machine's changes became version N. | Nothing. |
| `receber: N mudança(s) aplicada(s)` | Changes from another machine were applied here. Items prefixed with `-` were deleted (and are in `.sync_trash/`). | If a new MCP server, plugin or hook came in, see [After installing](setup.md#after-installing). |
| `enviar: outro PC enviou antes; buscando de novo` | Two machines synced at the same time. This one fetches the new version and redoes the merge. | Nothing. |
| `conflito em N arquivo(s): ficou a versão mais nova` | Both machines changed the same block, or one had an older copy. The newest version (modification time) won, and the other was saved. The message says which machine the discarded one came from. |
| `editado durante o sync, mantido como está (entra na próxima rodada)` | You edited the file while the round was running. The sync didn't write over it. | Nothing: the next round merges your edit with what came from the other machine. | If you want the other version, it's in `.sync_conflicts/<date>/`. Copy what you need and the next sync spreads it. |
| `N arquivos sumiram deste PC desde o último sync ... --allow-delete` | The mass-deletion guard fired: more than 20% of the files (and more than 10) disappeared at once. | Check that the `~/.claude` junctions are intact. If the deletion was intentional, run `python src/main.py --allow-delete` once. |
| `não consegui decifrar: a SYNC_KEY deste PC é diferente da dos outros` | The `SYNC_KEY` in `.env` doesn't match. | Copy the right `SYNC_KEY` from another machine. |
| `os dados estão criptografados: defina a mesma SYNC_KEY dos outros PCs` | This machine has no `SYNC_KEY`. | Add it to `.env`. |
| `SYNC_KEY precisa ter 32 bytes em base64` | The key was copied partially. | Copy the whole line. |
| `já existe uma sincronização rodando neste PC; saindo` | Autostart is in the middle of a round. | Normal when you run it by hand. Try again in a moment. |
| `SERVER_URL não definida` / `GIT_REPO_URL não definida` | `.env` is missing or lacks that line. | Create `.env` ([setup.md](setup.md#5-create-env)). |
| `aviso: rode 'npm ci' em <folder>` | A skill with `package.json` arrived without `node_modules` (which isn't synced). | `cd <folder>; npm ci` |
| `[aviso] <folder> e <folder> têm conteúdo: junte manualmente` | `~/.claude/<folder>` is a real folder and the sharing one also has content. | Move the files from `~/.claude/<folder>` into `claude_memory_sharing/<folder>`, delete the empty folder and run again. |

## HTTP codes (server mode)

| Code | Cause | Fix |
|---|---|---|
| `401` | Public key missing or wrong in `CLIENT_PUBLIC_KEYS`, `CLIENT_ID` different from the name used there, or the machine's clock is off by more than 5 min. | Check all three. |
| `413` | Upload larger than `MAX_UPLOAD_MB` (default 50). | Raise the variable on the server or remove large files from the sharing folder. |
| `415` or `formato antigo ou inválido` | Client and server on different protocol versions. | Update both: `git pull` + restart the sync, and deploy the server. |
| `429` | 10 authentication failures in 15 min from this IP. | Fix `.env` and wait 15 min. |
| `503` | The server is already processing 2 heavy uploads. | Nothing: the next round retries. |
| `não consegui falar com ...` | Server down, wrong URL or no internet. | Check `SERVER_URL` and the service on Railway. With sleep enabled, the first request can take a few extra seconds. |

`409` never shows up as an error: the client handles it by fetching again or resending what was missing.

## Git mode

Errors from the serverless mode (`git push falhou`, `git clone falhou`, `terminal prompts disabled` and others): see the table in [git-backend.md](git-backend.md#common-problems).

## Situations

**I deleted a file and it disappeared from my other machines. I want it back.**
On each machine that received the deletion, it's in `.sync_trash/<date>/<path>`. Copy it back to `~/.claude/claude_memory_sharing/<path>`: the next sync spreads it to the others.

**A file came back after I deleted it.**
Another machine edited it after you deleted it (edit beats deletion). Delete it again: if nobody touches it, the deletion propagates.

**A change from another machine didn't arrive.**
The other machine has to send first, then this one has to receive. Run `python src/main.py` on the other machine, then here. Its log should show `enviar: versão N`.

**I want to rebuild the base from scratch.**
Delete `SHARING_MEMORY/` and run `python src/main.py`. With no base, nothing is deleted: what exists on only one side is added to the other, and a file that differs keeps the newest version (the other goes to `.sync_conflicts/`).

**One of my edits was replaced by an old version.**
The discarded version is in `.sync_conflicts/<date>/` (on the machine that discarded it): copy it back. Also check that both machines' clocks are right, since "newest" is decided by modification time.

### Rotating the SYNC_KEY

Use this when you lost the key or a machine was compromised.
1. Stop the sync on every machine.
2. Generate a new key: `python src/components/remote_client.py synckey`.
3. Put the new `SYNC_KEY` into every machine's `.env`.
4. Empty the backend:
   - **server:** `TRUNCATE cms_versions, cms_blobs;` on Postgres;
   - **Git:** delete and recreate the repository.
5. Run `python src/main.py` on one machine (it uploads everything with the new key), then on the others.

The bases (`SHARING_MEMORY/`) stay valid: the machines only merge what changed.

**A merge broke `settings.json` or `~/.claude.json`.**
Before changing those files, the sync keeps the previous version in `settings.json.bak-sync` and `.claude.json.bak-sync`. The same goes for `CLAUDE.md.bak-sync`.

**An MCP server doesn't connect after syncing.**
It almost always came from another machine and points to a program that doesn't exist here. Check its `command` in `~/.claude.json` → `mcpServers` and install the program. Don't delete the entry: the deletion would propagate to the machine that uses it.

**I changed the code and the sync behaves the same.**
Autostart keeps the old process loaded. Restart it: `./install_autostart.sh --restart` (macOS/Linux) or `restart_sync.bat` (Windows).

**Testing without affecting other machines.**
- `python src/main.py --no-upload` receives and applies without sending. Local changes go out on the next normal round.
- `python src/main.py --offline` only builds `NEW_MEMORY`, with no network.
