#!/usr/bin/env bash
# Instala o modo quiosque no servidor (rodar uma vez, no Dell, como o usuário lucas; pede a senha do sudo):
#   bash ~/hub-musical/deploy/install-kiosk.sh
# Para desfazer: bash ~/hub-musical/deploy/install-kiosk.sh --remover
set -euo pipefail

OVERRIDE=/etc/systemd/system/getty@tty1.service.d/autologin.conf
MARK="# HUB Musical: modo quiosque"

if [ "${1:-}" = "--remover" ]; then
  sudo rm -f "$OVERRIDE"
  [ -f ~/.bash_profile ] && sed -i "/$MARK/,+1d" ~/.bash_profile
  sudo systemctl daemon-reload
  sudo systemctl restart getty@tty1
  echo "Modo quiosque removido. A tela do Dell volta a mostrar o login em texto."
  exit 0
fi

echo "1/3 Instalando cage (tela cheia mínima) e chromium (navegador)..."
sudo apt install -y cage chromium

echo "2/3 Login automático do usuário $USER na tela do Dell (tty1)..."
sudo mkdir -p "$(dirname "$OVERRIDE")"
printf '[Service]\nExecStart=\nExecStart=-/sbin/agetty --autologin %s --noclear %%I $TERM\n' "$USER" |
  sudo tee "$OVERRIDE" >/dev/null

echo "3/3 Abrindo o quiosque ao entrar na tela do Dell (só no tty1; o SSH não é afetado)..."
touch ~/.bash_profile
grep -q '\.profile' ~/.bash_profile || sed -i '1i [ -f ~/.profile ] && . ~/.profile' ~/.bash_profile
if ! grep -q "$MARK" ~/.bash_profile; then
  {
    echo "$MARK"
    echo 'if [ "$(tty)" = "/dev/tty1" ] && [ -z "$WAYLAND_DISPLAY" ] && command -v cage >/dev/null; then exec ~/hub-musical/deploy/kiosk.sh; fi'
  } >> ~/.bash_profile
fi

sudo systemctl daemon-reload
sudo systemctl restart getty@tty1
echo "Pronto. Em alguns segundos a tela do Dell deve abrir o HUB em tela cheia."
