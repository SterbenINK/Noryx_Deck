"""Wake-on-LAN: какие MAC-адреса сообщить телефону, чтобы он мог разбудить этот ПК, когда тот уснёт.

Берём сетевую карту, через которую подключился телефон (адрес ПК в этом соединении), и другие карты ПК
в той же подсети (ПК в сети и кабелем, и по Wi-Fi). Сами «магические пакеты» шлёт приложение на телефоне
(APK 1.2+), когда до ПК не достучаться. Общий модуль для Windows и Linux (psutil)."""
import ipaddress
import re
import socket

import psutil

LINK = getattr(psutil, "AF_LINK", -1)        # MAC: на Windows psutil.AF_LINK = -1, на Linux — AF_PACKET (17)


def norm_mac(s):
    """«2c-f0-5d-12-34-56» / «2c:f0:5d:12:34:56» → «2C:F0:5D:12:34:56». Не MAC сетевой карты (нули, широковещательный
    или групповой адрес) — None."""
    h = re.sub(r"[^0-9A-Fa-f]", "", s or "")
    if len(h) != 12 or int(h, 16) == 0 or int(h[:2], 16) & 1:
        return None
    return ":".join(h[i:i + 2] for i in range(0, 12, 2)).upper()


def plain_ip(ip):
    """«::ffff:192.168.0.5» → 192.168.0.5, «fe80::1%eth0» → fe80::1; не адрес — None."""
    try:
        a = ipaddress.ip_address(str(ip or "").split("%")[0])
    except ValueError:
        return None
    if a.version == 6 and a.ipv4_mapped:
        return a.ipv4_mapped
    return a


def _net(ip, mask):
    try:
        return ipaddress.ip_network(f"{ip}/{mask}", strict=False) if mask else None
    except ValueError:
        return None


def _cards(addrs):
    """[(имя карты, MAC или None, [(адрес, подсеть или None)])]"""
    out = []
    for nic, lst in addrs.items():
        mac = next(filter(None, (norm_mac(a.address) for a in lst if a.family == LINK)), None)
        ips = []
        for a in lst:
            if a.family in (socket.AF_INET, socket.AF_INET6):
                ip = plain_ip(a.address)
                if ip is not None:
                    ips.append((ip, _net(ip, a.netmask)))
        out.append((nic, mac, ips))
    return out


def macs_for(local_ip, addrs=None):
    """Карты ПК для пробуждения по адресу, к которому подключился телефон: [{"mac", "nic", "ip"}], первой — карта
    с этим адресом, за ней — другие карты в той же подсети. Пусто — адрес не наш или петля (127.0.0.1)."""
    me = plain_ip(local_ip)
    if me is None or me.is_loopback:
        return []
    cards = _cards(psutil.net_if_addrs() if addrs is None else addrs)
    main = next((c for c in cards if any(ip == me for ip, _ in c[2])), None)
    if main is None or not main[1]:
        return []
    net = next((n for ip, n in main[2] if ip == me), None)
    out = [{"mac": main[1], "nic": main[0], "ip": str(me)}]
    for nic, mac, ips in cards:
        if nic == main[0] or not mac or any(o["mac"] == mac for o in out) or net is None:
            continue
        hit = next((ip for ip, _ in ips if ip.version == net.version and ip in net), None)
        if hit is not None:
            out.append({"mac": mac, "nic": nic, "ip": str(hit)})
    return out


def subnet(local_ip, addrs=None):
    """Подсеть ПК, через которую подключился телефон («192.168.0.0/24») — для подсказки в настройках."""
    me = plain_ip(local_ip)
    if me is None:
        return ""
    for _, _, ips in _cards(psutil.net_if_addrs() if addrs is None else addrs):
        for ip, net in ips:
            if ip == me and net is not None:
                return str(net)
    return ""


def magic(mac):
    """Магический пакет: 6 байт 0xFF и 16 раз MAC (так же собирает приложение — сверяем в тестах)."""
    m = norm_mac(mac)
    if not m:
        raise ValueError(f"не MAC: {mac!r}")
    return b"\xff" * 6 + bytes.fromhex(m.replace(":", "")) * 16
