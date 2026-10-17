"""
Sincroniza settings e MCPs do Claude Code com a pasta claude_memory_sharing.

Lê ~/.claude/settings.json e ~/.claude.json e gera, COM os segredos e com os
caminhos do usuário trocados por tokens (para funcionar em outro PC):
  - settings.template.json  (settings completo, env incluso)
  - mcp.template.json       (servidores MCP com env/headers/tokens)
  - plugins-install.txt     (comandos /plugin para reinstalar os plugins ativos)

Tokens de caminho (o install.ps1 expande para o usuário do outro PC):
  {{HOME}}        C:\\Users\\fulano
  {{HOME_FWD}}    C:/Users/fulano
  {{HOME_POSIX}}  /c/Users/fulano
  {{HOME_ESC}}    C:\\\\Users\\\\fulano  (barras escapadas, como nas regras de permissão)

ATENÇÃO: os templates gerados contêm chaves de API. Não publique a pasta.

Uso:
  python sync_claude_templates.py            # sincroniza uma vez
  python sync_claude_templates.py --watch    # fica rodando e sincroniza a cada mudança
  python sync_claude_templates.py --watch --interval 10
"""

import argparse
import copy
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
CLAUDE_DIR = HOME / ".claude"
SHARING_DIR = CLAUDE_DIR / "claude_memory_sharing"
SETTINGS_SRC = CLAUDE_DIR / "settings.json"
CLAUDE_JSON_SRC = HOME / ".claude.json"
# CLAUDE.md global: viaja direto de/para ~/.claude/CLAUDE.md, sem cópia no sharing
CLAUDE_MD = CLAUDE_DIR / "CLAUDE.md"
CLAUDE_MD_NAME = "CLAUDE.md"

# Pastas portáteis: ficam em claude_memory_sharing e ~/.claude/<pasta> é uma junction
# para lá. Para compartilhar uma pasta nova, é só acrescentar aqui.
PORTABLE_DIRS = ("skills", "agents", "commands", "output-styles", "design-references", "hooks", "mods")

# Formas em que o caminho do usuário aparece nas configs
_home_win = str(HOME)                                   # C:\Users\fulano
_home_fwd = _home_win.replace("\\", "/")                # C:/Users/fulano
_drive, _rest = _home_fwd.split(":", 1) if ":" in _home_fwd else ("", _home_fwd)
_home_posix = f"/{_drive.lower()}{_rest}" if _drive else _home_fwd   # /c/Users/fulano
_home_esc = _home_win.replace("\\", "\\\\")             # C:\\Users\\fulano


def _re(text: str):
    return re.compile(re.escape(text), re.IGNORECASE)


# A ordem importa: a forma escapada contém as barras da forma normal
HOME_FORMS = [
    (_re(_home_esc), "{{HOME_ESC}}"),
    (_re(_home_win), "{{HOME}}"),
    (_re(_home_fwd), "{{HOME_FWD}}"),
    (_re(_home_posix), "{{HOME_POSIX}}"),
]
# Em Markdown não há quem expanda tokens: usa ~ (ou $HOME dentro de aspas,
# onde o shell não expande ~)
MD_HOME_RE = re.compile("|".join(p.pattern for p, _ in HOME_FORMS), re.IGNORECASE)

NODE_EXE_RE = re.compile(r'"?[A-Za-z]:[\\/]Program Files[\\/]nodejs[\\/]node\.exe"?', re.IGNORECASE)


def load_json(path: Path):
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def tokenize_home(text: str) -> str:
    for pattern, token in HOME_FORMS:
        text = pattern.sub(lambda _: token, text)
    return text


def replace_home(obj):
    """Troca o caminho do usuário pelos tokens {{HOME*}} em todas as strings."""
    if isinstance(obj, str):
        return tokenize_home(obj)
    if isinstance(obj, list):
        return [replace_home(v) for v in obj]
    if isinstance(obj, dict):
        return {k: replace_home(v) for k, v in obj.items()}
    return obj


_TOKEN_VALUES = {
    "{{HOME_ESC}}": _home_esc,
    "{{HOME_FWD}}": _home_fwd,
    "{{HOME_POSIX}}": _home_posix,
    "{{HOME}}": _home_win,
}


def expand_home(obj):
    """Inverso de replace_home: troca os tokens {{HOME*}} pelo caminho deste PC."""
    if isinstance(obj, str):
        for token, value in _TOKEN_VALUES.items():
            obj = obj.replace(token, value)
        return obj
    if isinstance(obj, list):
        return [expand_home(v) for v in obj]
    if isinstance(obj, dict):
        return {k: expand_home(v) for k, v in obj.items()}
    return obj


def portable_markdown(text: str) -> str:
    """Troca o caminho do usuário por ~ (ou $HOME logo após aspas) em texto Markdown."""
    def repl(m):
        before = text[m.start() - 1] if m.start() else ""
        return "$HOME" if before in ('"', "'") else "~"
    return MD_HOME_RE.sub(repl, text)


def portable_settings(settings: dict) -> dict:
    s = copy.deepcopy(settings)

    # node.exe com caminho fixo -> "node" (resolve pelo PATH no outro PC)
    hooks = s.get("hooks")
    if isinstance(hooks, dict):
        for groups in hooks.values():
            for group in groups or []:
                for h in group.get("hooks", []):
                    if isinstance(h.get("command"), str):
                        h["command"] = NODE_EXE_RE.sub("node", h["command"])

    return replace_home(s)


def portable_mcp(claude_json: dict) -> dict:
    return replace_home({"mcpServers": copy.deepcopy(claude_json.get("mcpServers") or {})})


def plugin_commands(settings: dict) -> str:
    markets = settings.get("extraKnownMarketplaces") or {}
    lines = []
    for plugin, enabled in (settings.get("enabledPlugins") or {}).items():
        if not enabled:
            continue
        market = plugin.split("@", 1)[1] if "@" in plugin else ""
        source = (markets.get(market) or {}).get("source") or {}
        repo = source.get("repo") or source.get("url")
        if repo:
            lines.append(f"/plugin marketplace add {repo}")
        lines.append(f"/plugin install {plugin}")
    return "\n".join(lines) + "\n"


def write_if_changed(path: Path, content: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    return True


def _is_link(path: Path) -> bool:
    # Junction do Windows não conta como symlink: checa o atributo de reparse point
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if os.name == "nt":
        return bool(st.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    return stat.S_ISLNK(st.st_mode)


def _make_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)


def ensure_structure() -> list:
    """
    Garante claude_memory_sharing/<pasta> e a junction ~/.claude/<pasta> para cada
    PORTABLE_DIRS. Pasta vazia não viaja no zip, então cada PC cria a sua aqui.
    Se ~/.claude/<pasta> já existir como pasta real, o conteúdo é movido para o
    sharing (só se lá estiver vazio; senão avisa e não mexe em nada).
    """
    created = []
    for name in PORTABLE_DIRS:
        target = SHARING_DIR / name
        link = CLAUDE_DIR / name

        if link.exists() and not _is_link(link):
            if target.exists() and any(target.iterdir()):
                print(f"[aviso] {link} e {target} têm conteúdo: junte manualmente", file=sys.stderr)
                continue
            if target.exists():
                target.rmdir()
            shutil.move(str(link), str(target))
            created.append(f"{name} (movida para o sharing)")

        if not target.exists():
            target.mkdir(parents=True)
            created.append(f"{name}/")
        if not link.exists():
            _make_link(link, target)
            created.append(f"~/.claude/{name} -> sharing")
    return created


def retire_sharing_claude_md() -> list:
    """
    Versões antigas guardavam uma cópia do CLAUDE.md no sharing. Se ela for mais nova
    que a global, vira a global; depois a cópia é apagada.
    """
    old = SHARING_DIR / CLAUDE_MD_NAME
    if not old.exists():
        return []
    if not CLAUDE_MD.exists() or old.stat().st_mtime > CLAUDE_MD.stat().st_mtime:
        shutil.copy2(old, CLAUDE_MD)
        old.unlink()
        return ["CLAUDE.md (cópia do sharing promovida a global)"]
    old.unlink()
    return ["CLAUDE.md (cópia antiga removida do sharing)"]


def sync() -> list:
    if not SHARING_DIR.exists():
        raise SystemExit(f"Pasta de sharing não encontrada: {SHARING_DIR}")

    changed = ensure_structure() + retire_sharing_claude_md()
    if SETTINGS_SRC.exists():
        settings = load_json(SETTINGS_SRC)
        if write_if_changed(SHARING_DIR / "settings.template.json",
                            json.dumps(portable_settings(settings), indent=2, ensure_ascii=False, sort_keys=True) + "\n"):
            changed.append("settings.template.json")
        if write_if_changed(SHARING_DIR / "plugins-install.txt", plugin_commands(settings)):
            changed.append("plugins-install.txt")

    if CLAUDE_JSON_SRC.exists():
        if write_if_changed(SHARING_DIR / "mcp.template.json",
                            json.dumps(portable_mcp(load_json(CLAUDE_JSON_SRC)), indent=2, ensure_ascii=False, sort_keys=True) + "\n"):
            changed.append("mcp.template.json")

    return changed


def mtimes() -> tuple:
    return tuple(p.stat().st_mtime if p.exists() else 0 for p in (SETTINGS_SRC, CLAUDE_JSON_SRC))


def main():
    parser = argparse.ArgumentParser(description="Sincroniza settings/MCP do Claude com a pasta de sharing.")
    parser.add_argument("--watch", action="store_true", help="fica rodando e sincroniza quando os arquivos mudarem")
    parser.add_argument("--interval", type=float, default=5.0, help="segundos entre verificações no modo --watch")
    args = parser.parse_args()

    def run_once():
        try:
            changed = sync()
        except (json.JSONDecodeError, OSError) as e:
            # .claude.json pode estar sendo escrito pelo Claude no mesmo instante
            print(f"[aviso] não consegui ler as configs agora: {e.__class__.__name__}", file=sys.stderr)
            return False
        stamp = time.strftime("%H:%M:%S")
        print(f"[{stamp}] atualizado: {', '.join(changed)}" if changed else f"[{stamp}] nada mudou")
        return True

    if not args.watch:
        sys.exit(0 if run_once() else 1)

    print(f"Observando {SETTINGS_SRC} e {CLAUDE_JSON_SRC} (Ctrl+C para sair)")
    last = None
    try:
        while True:
            current = mtimes()
            if current != last and run_once():
                last = current
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
