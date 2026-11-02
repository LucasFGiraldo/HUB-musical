#!/usr/bin/env bash
# Modo quiosque: abre a tela do HUB em tela cheia, sem barras nem cursor, na tela do Dell.
# Roda no tty1 (login automático), chamado pelo ~/.bash_profile. Se o navegador fechar, abre de novo.
#   cage      = "compositor" Wayland mínimo: mostra um único aplicativo em tela cheia, sem área de trabalho
#   chromium  = navegador em --kiosk (sem barra de endereço, abas ou menus)
# -m last: com um monitor HDMI ligado, usa só ele (sem HDMI, usa a tela do notebook)
URL="${HUB_KIOSK_URL:-http://127.0.0.1:8888/}"
PROFILE="$HOME/.local/share/hub-kiosk"

# Espera o servidor do HUB responder (no boot ele pode subir depois)
until (exec 3<>/dev/tcp/127.0.0.1/8888) 2>/dev/null; do sleep 1; done

while true; do
  rm -rf "$PROFILE"  # perfil limpo: sem "restaurar páginas" nem avisos de falha
  cage -m last -- chromium \
    --kiosk --incognito --no-first-run --noerrdialogs --disable-infobars \
    --disable-session-crashed-bubble --disable-translate --disable-features=Translate \
    --disable-pinch --overscroll-history-navigation=0 --check-for-update-interval=31536000 \
    --password-store=basic --user-data-dir="$PROFILE" \
    --ozone-platform=wayland "$URL"
  sleep 3
done
