#!/usr/bin/env bash
# Faz o sync da memória do Claude rodar sozinho (a cada 15 min por padrão) no macOS e no Linux.
#   macOS: LaunchAgent do launchd (inicia no login, reinicia se cair)
#   Linux: serviço de usuário do systemd; sem systemd, uma linha @reboot no crontab
# O log vai para sync.log na raiz do projeto.
#
#   ./install_autostart.sh                 # instala e já inicia
#   ./install_autostart.sh --interval 600  # outro intervalo (segundos)
#   ./install_autostart.sh --restart       # reinicia (depois de um git pull)
#   ./install_autostart.sh --uninstall     # remove
set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.claude-memory-sync"
SERVICE="claude-memory-sync"
INTERVAL=900
ACTION=install

while [ $# -gt 0 ]; do
  case "$1" in
    --interval) INTERVAL="$2"; shift 2 ;;
    --uninstall) ACTION=uninstall; shift ;;
    --restart) ACTION=restart; shift ;;
    *) echo "opção desconhecida: $1" >&2; exit 1 ;;
  esac
done

if [ -x "$PROJECT/.venv/bin/python" ]; then
  PYTHON="$PROJECT/.venv/bin/python"
else
  PYTHON="$(command -v python3 || true)"
fi
[ -n "$PYTHON" ] || { echo "python3 não encontrado. Instale o Python 3.10+ ou crie a .venv do projeto." >&2; exit 1; }
ARGS=("$PROJECT/src/main.py" --loop --interval "$INTERVAL" --log-file)

# ------------------------------------------------------------------ macOS
mac() {
  local plist="$HOME/Library/LaunchAgents/$LABEL.plist" domain="gui/$(id -u)"
  case "$ACTION" in
    uninstall)
      launchctl bootout "$domain/$LABEL" 2>/dev/null || true
      rm -f "$plist"; echo "LaunchAgent $LABEL removido." ;;
    restart)
      launchctl kickstart -k "$domain/$LABEL" && echo "LaunchAgent $LABEL reiniciado." ;;
    install)
      mkdir -p "$(dirname "$plist")"
      {
        echo '<?xml version="1.0" encoding="UTF-8"?>'
        echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
        echo '<plist version="1.0"><dict>'
        echo "  <key>Label</key><string>$LABEL</string>"
        echo '  <key>ProgramArguments</key><array>'
        echo "    <string>$PYTHON</string>"
        for a in "${ARGS[@]}"; do echo "    <string>$a</string>"; done
        echo '  </array>'
        echo "  <key>WorkingDirectory</key><string>$PROJECT</string>"
        echo '  <key>RunAtLoad</key><true/>'
        echo '  <key>KeepAlive</key><true/>'
        echo '  <key>ThrottleInterval</key><integer>60</integer>'
        echo "  <key>StandardErrorPath</key><string>$PROJECT/sync.log</string>"
        echo '</dict></plist>'
      } > "$plist"
      launchctl bootout "$domain/$LABEL" 2>/dev/null || true
      launchctl bootstrap "$domain" "$plist"
      echo "LaunchAgent $LABEL instalado e iniciado (a cada $INTERVAL s, ao entrar no macOS)." ;;
  esac
}

# ------------------------------------------------------------------ Linux (systemd)
systemd_user() {
  local unit="$HOME/.config/systemd/user/$SERVICE.service"
  case "$ACTION" in
    uninstall)
      systemctl --user disable --now "$SERVICE" 2>/dev/null || true
      rm -f "$unit"; systemctl --user daemon-reload; echo "Serviço $SERVICE removido." ;;
    restart)
      systemctl --user restart "$SERVICE" && echo "Serviço $SERVICE reiniciado." ;;
    install)
      mkdir -p "$(dirname "$unit")"
      cat > "$unit" <<EOF
[Unit]
Description=Claude Memory Sync
After=network-online.target

[Service]
WorkingDirectory=$PROJECT
ExecStart="$PYTHON" "$PROJECT/src/main.py" --loop --interval $INTERVAL --log-file
Restart=always
RestartSec=60

[Install]
WantedBy=default.target
EOF
      systemctl --user daemon-reload
      systemctl --user enable --now "$SERVICE"
      echo "Serviço $SERVICE instalado e iniciado (a cada $INTERVAL s, ao entrar no Linux)."
      echo "Para rodar mesmo sem login aberto: loginctl enable-linger $USER" ;;
  esac
}

# ------------------------------------------------------------------ Linux (cron)
cron() {
  local tag="# claude-memory-sync" line
  line="@reboot cd \"$PROJECT\" && \"$PYTHON\" \"$PROJECT/src/main.py\" --loop --interval $INTERVAL --log-file $tag"
  local current; current="$(crontab -l 2>/dev/null | grep -v "$tag" || true)"
  # Só o processo do python (não um shell que por acaso tenha o caminho na linha de comando)
  stop() { pkill -f "^[^ ]*python[^ ]* $PROJECT/src/main.py --loop" 2>/dev/null || true; }
  start() { (cd "$PROJECT" && nohup "$PYTHON" "${ARGS[@]}" >/dev/null 2>&1 &); }
  case "$ACTION" in
    uninstall)
      if [ -n "$current" ]; then printf '%s\n' "$current" | crontab -; else crontab -r 2>/dev/null || true; fi
      stop; echo "Entrada do crontab removida." ;;
    restart) stop; start; echo "Sync reiniciado." ;;
    install)
      { [ -n "$current" ] && printf '%s\n' "$current"; printf '%s\n' "$line"; } | crontab -
      stop; start
      echo "Instalado no crontab (@reboot) e iniciado (a cada $INTERVAL s)." ;;
  esac
}

case "$(uname -s)" in
  Darwin) mac ;;
  Linux)
    if command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
      systemd_user
    else
      cron
    fi ;;
  *) echo "Sistema não suportado por este script. No Windows use install_autostart.ps1." >&2; exit 1 ;;
esac
echo "Log: $PROJECT/sync.log"
