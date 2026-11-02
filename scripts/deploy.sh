#!/usr/bin/env bash
# Envia o último commit (HEAD) para o servidor e reinicia o serviço.
# Uso (Git Bash, no Lenovo): scripts/deploy.sh
set -euo pipefail

HOST=${HUB_HOST:-lucas@hub-automacoes.local}
KEY=${HUB_KEY:-$HOME/.ssh/hub_ed25519}

cd "$(dirname "$0")/.."
git archive HEAD | ssh -i "$KEY" -o BatchMode=yes "$HOST" '
  set -e
  tar -x -C ~/hub-musical
  cd ~/hub-musical
  .venv/bin/pip install -q -r backend/requirements.txt
  mkdir -p ~/.config/systemd/user
  cp deploy/hub-musical.service ~/.config/systemd/user/
  systemctl --user daemon-reload
  systemctl --user enable hub-musical >/dev/null 2>&1
  systemctl --user restart hub-musical
  sleep 2
  systemctl --user is-active hub-musical
'
