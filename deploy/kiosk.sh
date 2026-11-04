#!/usr/bin/env bash
# Modo quiosque: abre a tela do HUB em tela cheia, sem barras nem cursor, na tela do Dell.
# Roda no tty1 (login automático), chamado pelo ~/.bash_profile. Se o navegador fechar, abre de novo.
#   cage      = "compositor" Wayland mínimo: mostra um único aplicativo em tela cheia, sem área de trabalho
#   chromium  = navegador em --kiosk (sem barra de endereço, abas ou menus)
# -m last: com um monitor HDMI ligado, usa só ele (sem HDMI, usa a tela do notebook)
URL="${HUB_KIOSK_URL:-http://127.0.0.1:8888/?quiosque}"  # ?quiosque esconde o cursor
PROFILE="$HOME/.local/share/hub-kiosk"

# Cursor do mouse invisível: tema de cursor com um pixel transparente (o cage desenha o cursor do sistema,
# que o CSS da página não alcança). Gerado uma vez em ~/.icons/hub-sem-cursor.
THEME="$HOME/.icons/hub-sem-cursor/cursors"
if [ ! -f "$THEME/left_ptr" ]; then
  mkdir -p "$THEME"
  python3 - "$THEME/left_ptr" <<'PY'
import struct, sys
chunk = struct.pack("<10I", 36, 0xfffd0002, 1, 1, 1, 1, 0, 0, 0, 0)  # 1x1, pixel transparente
data = b"Xcur" + struct.pack("<3I", 16, 1, 1) + struct.pack("<3I", 0xfffd0002, 1, 28) + chunk
open(sys.argv[1], "wb").write(data)
PY
  for n in default arrow pointer text xterm hand1 hand2 watch wait grab grabbing; do ln -sf left_ptr "$THEME/$n"; done
  printf '[Icon Theme]
Name=hub-sem-cursor
' > "$HOME/.icons/hub-sem-cursor/index.theme"
fi
export XCURSOR_THEME=hub-sem-cursor XCURSOR_SIZE=1 XCURSOR_PATH="$HOME/.icons"

# Ajustes por máquina sem mexer neste script (ex.: HUB_KIOSK_FLAGS, WLR_DRM_DEVICES): ~/.config/hub-kiosk.env
[ -f "$HOME/.config/hub-kiosk.env" ] && . "$HOME/.config/hub-kiosk.env"

# Espera o servidor do HUB responder (no boot ele pode subir depois)
until (exec 3<>/dev/tcp/127.0.0.1/8888) 2>/dev/null; do sleep 1; done

while true; do
  rm -rf "$PROFILE"  # perfil limpo: sem "restaurar páginas" nem avisos de falha
  cage -m last -- chromium \
    --kiosk --incognito --no-first-run --noerrdialogs --disable-infobars \
    --disable-session-crashed-bubble --disable-translate --disable-features=Translate \
    --disable-pinch --overscroll-history-navigation=0 --check-for-update-interval=31536000 \
    --password-store=basic --user-data-dir="$PROFILE" \
    --ozone-platform=wayland ${HUB_KIOSK_FLAGS:-} "$URL"
  sleep 3
done
