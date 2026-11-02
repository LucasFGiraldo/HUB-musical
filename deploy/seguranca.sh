#!/usr/bin/env bash
# Reforça a segurança do servidor (rodar uma vez, no Dell, como o usuário lucas; pede a senha do sudo):
#   bash ~/hub-musical/deploy/seguranca.sh
# Pode rodar de novo quando quiser (por exemplo, depois de abrir a porta de uma automação nova).
set -euo pipefail

if [ "$(id -u)" = 0 ]; then echo "Rode como o seu usuário (sem sudo na frente); o script pede a senha quando precisar."; exit 1; fi

echo "1/4 Atualizações de segurança automáticas e sqlite3 (para consultar o banco)..."
sudo apt install -y unattended-upgrades sqlite3
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' |
  sudo tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null

echo "2/4 Firewall: só a rede de casa acessa, e só as portas do HUB..."
TMP=$(mktemp)
cat > "$TMP" <<'EOF'
#!/usr/sbin/nft -f
# Firewall do HUB de automações (gerado por ~/hub-musical/deploy/seguranca.sh).
# Automação nova com página/porta própria: acrescente a porta em "portas_lan" e rode o script de novo.
# Emergência (bloqueou algo): sudo nft flush ruleset   (libera tudo até o próximo boot)
flush ruleset

define portas_lan = { 22, 8888 }   # 22 = SSH, 8888 = HUB Musical

table inet filter {
  set rede_local4 { type ipv4_addr; flags interval; elements = { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16 } }
  set rede_local6 { type ipv6_addr; flags interval; elements = { fc00::/7, fe80::/10 } }

  chain input {
    type filter hook input priority filter; policy drop;
    iif lo accept
    ct state established,related accept
    ct state invalid drop
    meta l4proto { icmp, ipv6-icmp } accept
    udp dport { 68, 546 } accept                                  # DHCP: receber IP do roteador
    ip saddr @rede_local4 udp dport 5353 accept                   # mDNS: hub-automacoes.local
    ip6 saddr @rede_local6 udp dport 5353 accept
    ip saddr @rede_local4 tcp dport $portas_lan accept
    ip6 saddr @rede_local6 tcp dport $portas_lan accept
  }
  chain forward { type filter hook forward priority filter; policy drop; }
  chain output { type filter hook output priority filter; }
}
EOF
sudo nft -c -f "$TMP"                     # confere a sintaxe antes de aplicar
sudo install -m 644 "$TMP" /etc/nftables.conf
rm -f "$TMP"
sudo nft -f /etc/nftables.conf
sudo systemctl enable nftables >/dev/null 2>&1

echo "3/4 SSH: sem login do root, só o usuário $USER..."
CONF=/etc/ssh/sshd_config.d/10-hub.conf
{
  echo "# HUB de automações (gerado por ~/hub-musical/deploy/seguranca.sh)"
  echo "PermitRootLogin no"
  echo "AllowUsers $USER"
  echo "MaxAuthTries 3"
  echo "X11Forwarding no"
  echo "KbdInteractiveAuthentication no"
  # Senha pelo SSH só sai se já houver uma chave cadastrada (senão você ficaria trancado para fora)
  if [ -s ~/.ssh/authorized_keys ]; then echo "PasswordAuthentication no"; fi
} | sudo tee "$CONF" >/dev/null
sudo sshd -t                              # confere a configuração antes de aplicar
sudo systemctl reload ssh

echo "4/4 Permissões: só você lê o projeto, os tokens e o banco..."
chmod 700 ~
chmod 750 ~/hub-musical
chmod 700 ~/hub-musical/data
chmod 600 ~/hub-musical/data/* ~/hub-musical/.env 2>/dev/null || true

echo
echo "Pronto. Firewall ativo, SSH só por chave, atualizações de segurança automáticas."
grep -q "PasswordAuthentication no" "$CONF" && echo "Lembrete: antes de vender o Lenovo, cadastre a chave do computador novo (o SSH não aceita mais senha)."
