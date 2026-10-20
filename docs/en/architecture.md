# Client architecture

- [The round](#the-round)
- [Folders and files](#folders-and-files)
- [Portable paths](#portable-paths)
- [3-way merge](#3-way-merge)
- [Deletions](#deletions)
- [Modification time, edits during a sync, and concurrency](#modification-time-edits-during-a-sync-and-concurrency)
- [Encryption](#encryption)
- [Backends](#backends)
- [Server protocol](#server-protocol)
- [Modules](#modules)

## The round

`src/main.py` runs one round, or one per interval with `--loop`:

```
refresh_local()    sync_claude_templates.sync()   links + settings/MCP templates
                   copy_sharing()                  ~/.claude/claude_memory_sharing → NEW_MEMORY/ (LOCAL)
sync_round()       (components/sync_engine.py)
  1. base   = SHARING_MEMORY/manifest.json        the version from the last sync
  2. remote = backend.latest(since=base.version)  nothing new? remote = base
  3. merge  = merge_snapshots(base, local, remote)
  4. guard  : too many local deletions → stop and ask for --allow-delete
  5. send   : if the result differs from remote → backend.put(base=remote.version, ...)
              Conflict (another machine got there first) → back to step 2 (up to 5 times)
  6. apply  : result → Claude folder, settings.json, ~/.claude.json, CLAUDE.md
  7. base   = result (or the remote, with --no-upload) → SHARING_MEMORY/
```

Applying to the Claude folder only happens after the upload is accepted. If the process dies midway, the next round redoes everything from the same base and gets the same result, because the merge is deterministic.

Only one round runs at a time (`.sync.lock`, an OS file lock).

## Folders and files

| Path | Written by | Contents |
|---|---|---|
| `~/.claude/claude_memory_sharing/` | You, Claude and the round | Portable memory: the `PORTABLE_DIRS` folders and the generated templates. |
| `~/.claude/<folder>` | `ensure_structure()` | Junction (Windows) or symlink to `claude_memory_sharing/<folder>`. |
| `NEW_MEMORY/` | `copy_sharing()` | LOCAL: mirror of the sharing folder + `CLAUDE.md`, with portable paths and no excluded files. |
| `SHARING_MEMORY/` | `save_base()` | BASE: full content of the last agreed version + `manifest.json`. |
| `.sync_conflicts/<date>/` | `sync_round()` | The losing version of each conflict. |
| `.sync_trash/<date>/` | `apply_local()` | Files deleted by another machine. |
| `.sync_repo/` | `GitStore` | Clone of the data repository (Git mode only). Just a cache. |
| `sync.log` | `main.py` | Autostart log. Rotates at 2 MB. |

Excluded (`remote_client.is_excluded`): `node_modules/`, `.git/`, `__pycache__/` and `skills/synced/`.

To share a new `~/.claude` folder, add its name to `PORTABLE_DIRS` (`sync_claude_templates.py`).

Deleting `SHARING_MEMORY/` is safe. With no base, the next merge treats everything as "added on both sides": nothing is removed, and conflicts only happen where the two machines have different versions of the same block.

## Portable paths

The same file has to work under `C:\Users\ana` and `C:\Users\bruno`.

| Where | In NEW_MEMORY (what travels) | On the way back |
|---|---|---|
| `.md` | `C:\Users\ana` → `~` (or `$HOME` right after quotes) | The merged file is written that way, with `~`. |
| `settings.template.json`, `mcp.template.json` | `{{HOME}}`, `{{HOME_FWD}}` (`C:/Users/ana`), `{{HOME_POSIX}}` (`/c/Users/ana`), `{{HOME_ESC}}` (`C:\\Users\\ana`). Keys are sorted so the text is stable. | `expand_home()` swaps in this machine's path. |
| Hooks using `C:\Program Files\nodejs\node.exe` | Become `node` | Resolved through PATH. |

Templates aren't written back as files. The round computes the difference between the local template and the merged one, and applies **only that difference** to the real `settings.json` and to the `mcpServers` in `~/.claude.json`, with a per-key 3-way merge. Anything that exists only on this machine (the full `node.exe` path, counters in `~/.claude.json`) stays untouched.

## 3-way merge

`merge3.py`, per file, with base B, local L and remote R (`None` = file absent):

| Case | Result |
|---|---|
| L = R | L |
| L = B | R (only the remote changed, deletions included) |
| R = B | L (only this machine changed, deletions included) |
| L absent and R changed / R absent and L changed | Whoever changed it (edit beats deletion) |
| Text changed on both sides | Line merge (below) |
| JSON changed on both sides | Recursive per-key merge; lists per item, like lines |
| Binary changed on both sides | Conflict: the newest wins |
| Only one side changed, but to content **older** than the other side's file | Stale copy: the newest wins |

**Text.** It computes each side's changed blocks relative to the base (`difflib`) and groups the ones that overlap:
- a block changed by only one side → applied;
- both sides produced the same result → applied once;
- both sides **only inserted** at the same spot → both blocks are kept whole, the remote one first (the common case of appending a section at the end of a file). Nothing is filtered or reordered;
- otherwise → **conflict**: the block from the **newest** side (modification time) stays, and the other side's whole version goes to `.sync_conflicts/`.

With no base for the file (new machine, deleted base) and different content on both sides, a line merge would duplicate content: the newest whole file wins.

## Deletions

With a base, "not here" has two different meanings:
- **it was in the base and is gone** → it was deleted, and the deletion propagates;
- **it wasn't in the base** → it's new on the other side, and it comes in.

Safeguards:
- **Mass-deletion guard** (`mass_delete_limit`): if this machine deleted more than `max(10, 20% of the base)` files since the last sync, the round stops with `SyncAborted` and asks for `--allow-delete`. This keeps a broken junction or an empty folder from wiping the other machines.
- **Trash:** a file deleted by another machine is moved to `.sync_trash/<date>/`, never deleted.
- **Edit beats deletion:** if one machine deleted a file and the other edited it, the file stays.

## Modification time, edits during a sync, and concurrency

**The newest always wins.** Each file travels with its modification time (`mtime` in the manifest). It decides:
- real conflicts (both sides changed the same block);
- **stale copies**: if one side "changed" a file to content older than the other side's (by more than 2 s), that change is discarded. This stops a machine that never received the new version from spreading the old one.

For times to be comparable across machines, a received file is written with the time of the original edit (not the time it arrived), and the copy into `NEW_MEMORY` preserves times. Machine clocks must be right.

**Edits during a sync.** A few seconds can pass between reading the folder and writing the result (the server may be waking up). Right before writing each file, the round checks it is still what was read. If you edited it in the meantime, it isn't touched, and the base goes back to the version that was read: the next round sees "both changed" and merges your edit with what came from the other machine.

**Config templates.** After `settings.template.json` and `mcp.template.json` are applied to the real configs, the base stores this machine's regenerated template. Differences that only exist on one machine (e.g. the `node.exe` path) don't bounce back and forth every round. Templates are always written with LF, so they hash the same on Windows and macOS/Linux.

**Concurrency** (backend version number):
- every upload states `base` = the remote version the merge was based on, and `version = base + 1`;
- the backend only accepts it if `base` is still the newest version (server: `409 conflict`; Git: push rejected). Otherwise the machine fetches the new version and redoes the merge;
- the version number only stops a machine from uploading on top of a version it hasn't seen. Who wins a conflict is decided by modification time, above.

## Encryption

`crypto_codec.py`, enabled by `SYNC_KEY` (32 random bytes in base64, the same on every machine):

| Item | Without `SYNC_KEY` | With `SYNC_KEY` |
|---|---|---|
| Content and manifest | `P1` + deflate | `E1` + nonce (12 bytes) + AES-256-GCM(deflate), with the blob id (or `"manifest"`) as associated data |
| Blob id | sha256 of the content | HMAC-SHA256 of the content |
| The backend sees | Readable content | Only random bytes, opaque ids and sizes |

The encryption and id keys are derived from `SYNC_KEY` with HKDF-SHA256 (different `info`), so one doesn't reveal the other.

The (encrypted) manifest records the `codec` used. When a machine starts using `SYNC_KEY`, it doesn't reuse blobs from unencrypted versions and re-uploads everything encrypted. The backend deletes the old blobs once they fall out of the last 3 versions.

Inside the envelope, the manifest looks like this:

```json
{ "format": 2, "version": 21, "codec": "aes-256-gcm",
  "files": { "skills/x/SKILL.md": { "sha256": "…", "size": 1234, "id": "…" } } }
```

## Backends

`main.py` picks the backend from `.env`:

| `.env` | Class | Where versions are stored |
|---|---|---|
| `SERVER_URL=...` (default) | `remote_client.Client` | HTTP server (protocol below) |
| `SYNC_BACKEND=git` + `GIT_REPO_URL=...` | `git_backend.GitStore` | Private Git repository ([git-backend.md](git-backend.md)) |

Both classes share the same interface:

| Method | What it does |
|---|---|
| `latest(since)` | `(newest version or None, sealed manifest, or None if it's since itself)` |
| `get_blobs(ids)` | `{id: sealed blob}` |
| `put(base, version, manifest, refs, blobs)` | Stores the version. Raises `Conflict` or `MissingOnServer`. |

## Server protocol

Every request is signed with Ed25519: `METHOD\nPATH\nTIMESTAMP_MS\nSHA256_HEX(body)` in the `X-Client-Id`, `X-Timestamp` and `X-Signature` headers.

**Bundle** format: `[uint32 BE name length][name][uint32 BE length][data]`, repeated.

| Route | Request | Response |
|---|---|---|
| `GET /latest?since=N` | — | `204` if the newest is `N` or there's none; otherwise a bundle `[manifest]`. `X-Version-Id` always present (empty = empty backend). |
| `POST /blobs` | JSON `{"ids": [...]}` (batches of 500) | Bundle with the blobs found. |
| `POST /upload` | Bundle `[meta.json {base, version, refs}, manifest, <id>...]` with only new blobs | `201`, `409 {conflict: true, latest}` or `409 {missing: [...]}` |

The server doesn't read manifests or blobs. It only uses `refs` to know what each version uses and to delete what none uses. Details in the server repository (`docs/en/api.md`).

## Modules

| File | Responsibility |
|---|---|
| `src/main.py` | Orchestrates the round, loop, lock, log and backend choice. |
| `src/components/sync_claude_templates.py` | `PORTABLE_DIRS`, links, template generation, `{{HOME*}}` tokens. |
| `src/components/sync_engine.py` | Base, snapshot merge, guard, upload with retry, apply, trash, conflicts. |
| `src/components/merge3.py` | 3-way merge for text, JSON and binary. |
| `src/components/crypto_codec.py` | `SYNC_KEY`, seal/open, ids. |
| `src/components/remote_client.py` | Server backend, bundle, exclusions, `scan`, `keygen`, `synckey`. |
| `src/components/git_backend.py` | Git repository backend. |
