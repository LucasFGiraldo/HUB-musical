#!/usr/bin/env bash
# Desliga o touchpad e qualquer mouse do Dell para o sistema gráfico: sem ponteiro, o quiosque não desenha cursor.
# O teclado e o SSH não são afetados. Rodar uma vez, no Dell, como o usuário lucas (pede a senha do sudo):
#   bash ~/hub-musical/deploy/sem-mouse.sh
# Para voltar a usar mouse/touchpad: bash ~/hub-musical/deploy/sem-mouse.sh --remover
set -euo pipefail
RULE=/etc/udev/rules.d/90-hub-sem-mouse.rules

if [ "${1:-}" = "--remover" ]; then
  sudo rm -f "$RULE"
else
  printf '# HUB Musical: sem mouse nem touchpad (quiosque sem cursor)\nENV{ID_INPUT_MOUSE}=="1", ENV{LIBINPUT_IGNORE_DEVICE}="1"\nENV{ID_INPUT_TOUCHPAD}=="1", ENV{LIBINPUT_IGNORE_DEVICE}="1"\n' |
    sudo tee "$RULE" >/dev/null
fi
sudo udevadm control --reload
sudo udevadm trigger --subsystem-match=input --action=change
pkill -x cage || true   # o quiosque reabre sozinho em 3 s, já com a regra valendo
echo "Pronto. Em alguns segundos a tela do HUB reabre $([ "${1:-}" = "--remover" ] && echo "com o mouse de volta." || echo "sem cursor.")"
