# Serverless mode: Git repository

Use a **private** Git repository instead of the server. There's no deploy, no database and no Ed25519 key: each machine uses the Git credentials it already has.

Everything else is the same: the same folders, exclusions, 3-way merge, encryption and skill.

> [!IMPORTANT]
> Use `SYNC_KEY`. With it, the repository only stores encrypted data: neither content nor file names are visible, and GitHub never sees your API keys. Still, keep the repository **private** and separate from this code repository.

## Which mode to choose

| Choose **Git mode** if... | Choose the **server** if... |
|---|---|
| You want to start in 5 minutes and pay nothing | You already run the server |
| You already use GitHub/GitLab with SSH or Git Credential Manager | You want to revoke a machine without touching Git permissions |
| You want the full version history | You prefer a short history (3 versions) |

You can switch later (see [Switching modes](#switching-modes)).

## Step by step

### 1. Create an empty private repository

On GitHub: **New repository**, tick **Private**, and do **not** add a README, license or `.gitignore`. Example: `claude-memory-data`.

### 2. Check that this machine's Git can reach it

```bash
git ls-remote git@github.com:you/claude-memory-data.git
```

No error means you're ready (it may print nothing, since the repo is empty). If it asks for a password or fails, set up one of these:
- **SSH:** generate a key (`ssh-keygen -t ed25519`) and add it under GitHub → Settings → SSH keys.
- **HTTPS:** use a credential helper (Git Credential Manager on Windows, the Keychain on macOS). Clone once by hand so it stores your login.

The sync runs in the background and **cannot prompt for a password**: authentication has to work without interaction.

### 3. Clone the client

```bash
git clone https://github.com/le0nardomartins/ClaudeMemorySync.git
cd ClaudeMemorySync
python -m venv .venv
.venv/bin/pip install -r requirements.txt     # Windows: .venv/Scripts/pip
```

### 4. Generate the SYNC_KEY (once)

```bash
.venv/bin/python src/components/remote_client.py synckey
```

Use the **same** `SYNC_KEY` on every machine and keep it in a password manager.

### 5. Create `.env`

```ini
SYNC_BACKEND=git
GIT_REPO_URL=git@github.com:you/claude-memory-data.git
SYNC_KEY=...
# optional
GIT_BRANCH=main
CLIENT_ID=pc-laptop
```

`CLIENT_ID` only names the commits. Without it, the computer's hostname is used.

### 6. First round and autostart

```bash
.venv/bin/python src/main.py
./install_autostart.sh                                             # macOS / Linux
powershell -ExecutionPolicy Bypass -File install_autostart.ps1     # Windows
```

Expected output on the first round (log messages are in Portuguese):

```
[10:00:03] NEW_MEMORY: 13 itens prontos para envio
[10:00:09] enviar: versão 1 (578 arquivos), 6.20 MB enviados
```

### 7. Other machines

Repeat steps 2, 3, 5 and 6 on each machine with the **same** `GIT_REPO_URL` and the **same** `SYNC_KEY`. There's no key to register: the machine's Git just needs access to the repo.

## How it works

```
 Machine                                     Private Git repository
 ───────                                     ──────────────────────
 fetch    git fetch + reset  ◄────────────   meta.json       { "version": N }
                                             manifest.bin    encrypted manifest
                                             blobs/<id>      encrypted content
 merge    base × local × remote (on the machine)
 send     writes new blobs + manifest, commit "sync: pc-x -> versão N+1"
          git push  ──────────────────────►  rejected? another machine pushed first:
                                             fetch again and redo the merge (up to 5x)
```

- The ids in `blobs/` are HMACs (with `SYNC_KEY`): they reveal neither the content nor the file name.
- On each version, blobs it doesn't use leave the tree. They remain in Git history.
- The local clone lives in `.sync_repo/` in the project root. It's only a cache: delete it any time and the next sync clones again.
- `.gitattributes` with `* -text` makes sure Git never touches the bytes.

## Repository size

Git keeps the whole history. Encrypted data doesn't compress, so each changed file adds its own size to the history. For skills and configs that's small. If the repo ever gets too big, restart the history:

1. Stop the sync on every machine.
2. Delete and recreate the remote repository (empty).
3. On each machine, delete `.sync_repo/`.
4. Run `python src/main.py` on one machine, then on the others. The bases (`SHARING_MEMORY/`) stay valid.

## Switching modes

Use the same procedure in either direction (server → Git or Git → server):
1. Change `.env` on every machine.
2. Run the sync on one machine: the new backend is empty, so it uploads everything.
3. Run it on the other machines.

The local bases stay valid, so the machines only merge what changed.

## Common problems

| Message | Cause | Fix |
|---|---|---|
| `faltam no .env: GIT_REPO_URL` | `SYNC_BACKEND=git` without a URL. | Add `GIT_REPO_URL`. |
| `git não encontrado no PATH` | Git isn't installed, or isn't on the autostart's PATH. | Install Git and restart the autostart. |
| `git clone falhou: ... Permission denied` / `Authentication failed` | This machine's Git has no access to the repo. | Test with `git ls-remote <url>`. Set up SSH or a credential helper. |
| `git fetch falhou: ... terminal prompts disabled` | Git wanted to ask for a password, and the sync isn't interactive. | Clone once by hand so the credential helper stores the login, or use SSH. |
| `git push falhou: ...` | Push rejected for a reason other than concurrency (permissions, protected branch). | Check write access and that the branch isn't protected. |
| `outro PC enviou antes 5 vezes seguidas` | Several machines pushing at the same moment. | Nothing to do: the next round retries. |
