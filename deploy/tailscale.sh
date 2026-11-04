#!/usr/bin/env bash
# Acesso ao painel de fora de casa pelo Tailscale (rodar uma vez, no Dell, como o usuário lucas):
#   bash ~/hub-musical/deploy/tailscale.sh
# Nenhuma porta é aberta no roteador: o Dell e o celular entram numa rede privada só sua (criptografada).
# Depois, de qualquer lugar (com o app Tailscale ligado no celular): http://hub-automacoes:8888
set -euo pipefail

if [ "$(id -u)" = 0 ]; then echo "Rode como o seu usuário (sem sudo na frente); o script pede a senha quando precisar."; exit 1; fi
. /etc/os-release

echo "1/3 Instalando o Tailscale pelo repositório oficial (pkgs.tailscale.com, Debian $VERSION_CODENAME)..."
if ! command -v tailscale >/dev/null; then
  curl -fsSL "https://pkgs.tailscale.com/stable/debian/$VERSION_CODENAME.noarmor.gpg" |
    sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
  curl -fsSL "https://pkgs.tailscale.com/stable/debian/$VERSION_CODENAME.tailscale-keyring.list" |
    sudo tee /etc/apt/sources.list.d/tailscale.list >/dev/null
  sudo apt update
  sudo apt install -y tailscale
fi
sudo systemctl enable --now tailscaled

echo "2/3 Firewall: libera as portas do HUB também para a rede do Tailscale..."
bash "$(dirname "$0")/seguranca.sh"

echo "3/3 Entrando na sua conta Tailscale (abra o link que aparecer, no celular ou no computador)..."
sudo tailscale up --hostname="$(hostname -s)"

echo
echo "Pronto. Endereço do Dell no Tailscale: $(tailscale ip -4)"
echo "Painel de fora de casa: http://$(hostname -s):8888/painel (com o app Tailscale ligado)"
echo "Recomendado: no site do Tailscale (Machines), abra o Dell e escolha \"Disable key expiry\","
echo "senão ele pede login de novo a cada 180 dias."
