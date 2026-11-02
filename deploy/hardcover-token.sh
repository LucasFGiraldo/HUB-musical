#!/usr/bin/env bash
# Guarda o token da API do Hardcover no servidor (no .env, só legível por você) e reinicia o HUB.
# Rodar no Dell (pelo ssh hub):  bash ~/hub-musical/deploy/hardcover-token.sh
# O token não aparece na tela enquanto você cola. Token novo (expirou)? Rode de novo.
set -euo pipefail

ENV=~/hub-musical/.env
read -rsp "Cole o token do Hardcover e aperte Enter: " TOKEN; echo
TOKEN=${TOKEN#Bearer }   # o site mostra "Bearer ..."; guardamos só o token
TOKEN=$(printf %s "$TOKEN" | tr -d '[:space:]')
if [ ${#TOKEN} -lt 20 ]; then echo "Isso não parece um token (curto demais). Nada foi alterado."; exit 1; fi

touch "$ENV"; chmod 600 "$ENV"
grep -v '^HARDCOVER_TOKEN=' "$ENV" > "$ENV.tmp" || true
printf 'HARDCOVER_TOKEN=%s\n' "$TOKEN" >> "$ENV.tmp"
mv "$ENV.tmp" "$ENV"; chmod 600 "$ENV"

systemctl --user restart hub-musical
echo "Token guardado e HUB reiniciado."
