"""Presença do iPhone na rede de casa.

O atalho do iPhone chama POST /api/presence ao entrar no Wi-Fi: o servidor aprende o endereço físico (MAC)
do celular e marca "em casa". Depois, a cada minuto, confere se o celular ainda responde na rede (ARP,
que o iPhone responde mesmo com a tela apagada). Sem resposta por AWAY_AFTER, conta como fora de casa.
O MAC só é fixo se o "Endereço Wi-Fi privado" do iPhone estiver em "Fixo" (ou desligado) para esta rede.
"""

import asyncio
import ipaddress
import json
import logging
import shutil
import socket
import subprocess
import time

from . import config

FILE = config.DATA_DIR / "presence.json"
CHECK_SECONDS = 60
SWEEP_SECONDS = 5 * 60    # celular sumiu do endereço conhecido: procura na rede toda a cada 5 min
AWAY_AFTER = 15 * 60
SETTLE_SECONDS = 9        # tempo para o kernel perguntar (ARP) e o celular responder
IP = shutil.which("ip") or "/usr/sbin/ip"
log = logging.getLogger("hub.presence")

state = {"mac": None, "ip": None, "last_seen": None}
arrived = asyncio.Event()  # o histórico do Spotify é buscado na hora em que o iPhone volta
_last_sweep = 0.0


def is_home() -> bool | None:
    """None: presença não configurada (o atalho nunca rodou); a tela se comporta como antes."""
    if not state["mac"]:
        return None
    return bool(state["last_seen"]) and time.time() - state["last_seen"] < AWAY_AFTER


def status() -> dict:
    return {"configured": bool(state["mac"]), "home": is_home(), "last_seen": state["last_seen"], "ip": state["ip"]}


def _save() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(state))


def _load() -> None:
    try:
        state.update(json.loads(FILE.read_text()))
    except (OSError, ValueError):
        pass


def _neighbors() -> list[dict]:
    out = subprocess.run([IP, "-j", "-4", "neigh", "show"], capture_output=True, text=True, timeout=5).stdout
    return json.loads(out or "[]")


def _reachable(mac: str) -> str | None:
    """IP do aparelho se o kernel acabou de confirmar que ele respondeu."""
    for n in _neighbors():
        if (n.get("lladdr") or "").lower() == mac and "REACHABLE" in n.get("state", []):
            return n.get("dst")
    return None


def _poke(ips) -> None:
    """Um pacote UDP vazio (porta 9, "descarte") faz o kernel perguntar pelo aparelho na rede."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        for ip in ips:
            try:
                s.sendto(b"", (str(ip), 9))
            except OSError:
                pass


def _subnet() -> list:
    out = subprocess.run([IP, "-j", "-4", "addr", "show", "scope", "global"], capture_output=True, text=True, timeout=5).stdout
    for iface in json.loads(out or "[]"):
        for a in iface.get("addr_info", []):
            net = ipaddress.ip_interface(f"{a['local']}/{max(24, a['prefixlen'])}").network
            return [h for h in net.hosts() if str(h) != a["local"]]
    return []


def _check() -> None:
    global _last_sweep
    mac = state["mac"]
    if not mac:
        return
    seen = None
    if state["ip"]:
        _poke([state["ip"]])
        time.sleep(SETTLE_SECONDS)
        seen = _reachable(mac)
    if not seen and time.time() - _last_sweep > SWEEP_SECONDS:
        _last_sweep = time.time()
        _poke(_subnet())
        time.sleep(SETTLE_SECONDS)
        seen = _reachable(mac)
    if seen:
        state.update(ip=seen, last_seen=time.time())
        _save()


def learn(client_ip: str) -> dict:
    """Chamado pelo atalho do iPhone. Aprende o MAC na primeira vez; depois só aceita o mesmo aparelho."""
    _poke([client_ip])
    time.sleep(1)
    mac = next((n["lladdr"].lower() for n in _neighbors() if n.get("dst") == client_ip and n.get("lladdr")), None)
    if not mac:
        raise LookupError("não achei o aparelho na rede")
    if state["mac"] and state["mac"] != mac:
        raise PermissionError("outro aparelho já está registrado como o iPhone")
    state.update(mac=mac, ip=client_ip, last_seen=time.time())
    _save()
    log.warning("atalho do iPhone: em casa")
    return status()


def forget() -> None:
    state.update(mac=None, ip=None, last_seen=None)
    FILE.unlink(missing_ok=True)


async def run() -> None:
    _load()
    was_home = is_home()  # comparado entre voltas: a saída acontece pelo relógio, durante o sleep
    while True:
        try:
            await asyncio.to_thread(_check)
        except Exception:  # o laço não pode morrer
            log.exception("falha ao conferir a presença do iPhone")
        home = is_home()
        if home != was_home and home is not None:
            log.warning("iPhone %s", "chegou em casa" if home else "saiu de casa")
            if home:
                arrived.set()
        was_home = home
        await asyncio.sleep(CHECK_SECONDS)
