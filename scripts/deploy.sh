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
  chmod 750 ~/hub-musical; mkdir -p data; chmod 700 data; chmod 600 data/* .env 2>/dev/null || true
  mkdir -p ~/.config/systemd/user
  cp deploy/hub-musical.service ~/.config/systemd/user/
  systemctl --user daemon-reload
  systemctl --user enable hub-musical >/dev/null 2>&1
  systemctl --user restart hub-musical
  # Só dá como publicado quando o servidor responde (um erro na inicialização aparece aqui)
  for i in $(seq 20); do
    .venv/bin/python -c "import httpx; httpx.get(\"http://127.0.0.1:8888/health\").raise_for_status()" 2>/dev/null && { echo active; exit 0; }
    sleep 1
  done
  echo "O servidor não subiu. Últimas linhas do registro:"
  journalctl --user -u hub-musical -n 15 --no-pager
  exit 1
'
