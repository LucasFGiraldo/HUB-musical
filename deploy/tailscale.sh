#!/usr/bin/env bash
# Acesso ao painel de fora de casa pelo Tailscale (rodar uma vez, no Dell, como o usuário lucas):
#   bash ~/hub-musical/deploy/tailscale.sh
# Nenhuma porta é aberta no roteador: o Dell e o celular entram numa rede privada só sua (criptografada).
# Depois, de qualquer lugar (com o app Tailscale ligado no celular): http://hub-automacoes:8888
set -euo pipefail

if [ "$(id -u)" = 0 ]; then echo "Rode como o seu usuário (sem sudo na frente); o script pede a senha quando precisar."; exit 1; fi
. /etc/os-release

echo "1/4 Instalando o Tailscale pelo repositório oficial (pkgs.tailscale.com, Debian $VERSION_CODENAME)..."
if ! command -v tailscale >/dev/null; then
  curl -fsSL "https://pkgs.tailscale.com/stable/debian/$VERSION_CODENAME.noarmor.gpg" |
    sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
  curl -fsSL "https://pkgs.tailscale.com/stable/debian/$VERSION_CODENAME.tailscale-keyring.list" |
    sudo tee /etc/apt/sources.list.d/tailscale.list >/dev/null
  sudo apt update
  sudo apt install -y tailscale
fi
sudo systemctl enable --now tailscaled

echo "2/4 Firewall: libera as portas do HUB também para a rede do Tailscale..."
bash "$(dirname "$0")/seguranca.sh"

echo "3/4 Entrando na sua conta Tailscale (abra o link que aparecer, no celular ou no computador)..."
sudo tailscale up --hostname="$(hostname -s)"

echo "4/4 HTTPS do Tailscale (o painel funciona sem internet só por HTTPS)..."
echo "    Se aparecer um link para ativar o HTTPS, abra, ative e rode este script de novo."
sudo tailscale serve --bg http://127.0.0.1:8888
NOME=$(tailscale status --json | python3 -c "import json,sys; print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))")

echo
echo "Pronto. Endereço do Dell no Tailscale: $(tailscale ip -4)"
echo "Painel (em casa ou fora, com o app Tailscale ligado): https://$NOME/painel"
echo "No iPhone, abra esse endereço no Safari > Compartilhar > Adicionar à Tela de Início: vira um app que abre"
echo "sem internet; as páginas lidas ficam guardadas e vão para o HUB quando o celular conectar."
echo "Recomendado: no site do Tailscale (Machines), abra o Dell e escolha \"Disable key expiry\","
echo "senão ele pede login de novo a cada 180 dias."
