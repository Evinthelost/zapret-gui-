#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zapret Control — нормальный интерфейс вместо терминала.
Python 3.9+ / tkinter, без внешних зависимостей.
"""

import argparse
import ctypes
import ipaddress
import json
import os
import queue
import random
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------
APP_VERSION = "1.7"

if getattr(sys, "frozen", False):
    APP_ROOT = Path(sys.executable).resolve().parent
else:
    APP_ROOT = Path(__file__).resolve().parent
BIN = APP_ROOT / "bin"
LISTS = APP_ROOT / "lists"
UTILS = APP_ROOT / "utils"
WINWS = BIN / "winws.exe"
SETTINGS_FILE = UTILS / "gui-settings.json"
DNS_BACKUP_FILE = UTILS / "gui-dns-backup.json"
ENGINE_OUT = UTILS / "engine.out.log"
ENGINE_ERR = UTILS / "engine.err.log"
GUI_LOG = UTILS / "gui.log"

UTILS.mkdir(exist_ok=True)

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008

# --------------------------------------------------------------------------
# theme
# --------------------------------------------------------------------------
BG = "#0b0d12"
BG2 = "#080a0e"
CARD = "#151820"
CARD2 = "#1b1f29"
CARD_SEL = "#122822"
BORDER = "#272c39"
TEXT = "#e8ebf0"
MUTED = "#8990a0"
DIM = "#5e6574"
ACCENT = "#00d68f"
ACCENT_DARK = "#00a871"
ACCENT_SOFT = "#104034"
DANGER = "#ff5c5c"
WARN = "#ffb84d"
WHITE = "#ffffff"
HOVER = "#232833"

FONT = "Segoe UI"
MONO = "Consolas"

PROFILES = {
    "universal": ("Универсальный", "YouTube · Discord · Steam"),
    "youtube": ("YouTube", "только Google / видео"),
    "discord": ("Discord", "голос, вложения, CDN"),
    "steam": ("Steam + Dota", "магазин, чат, игры"),
    "max": ("Максимум", "все списки + ipset"),
}

VARIANTS = [
    "hostfakesplit",
    "fakemultisplit",
    "multisplit652",
    "fake",
    "multidisorder",
    "disorder",
    "split2",
]

VARIANT_TITLES = {
    "auto": "авто",
    "hostfakesplit": "hostfakesplit",
    "fakemultisplit": "fake+multisplit",
    "multisplit652": "multisplit 652",
    "fake": "fake TLS",
    "multidisorder": "fake+disorder",
    "disorder": "disorder",
    "split2": "split2",
}

CHECK_TARGETS = [
    ("DNS YouTube", "dns", "www.youtube.com"),
    ("YouTube", "url", "https://www.youtube.com"),
    ("YouTube CDN", "url", "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"),
    ("Steam Store", "url", "https://store.steampowered.com"),
    ("Steam Community", "url", "https://steamcommunity.com"),
    ("Steam Help", "url", "https://help.steampowered.com"),
    ("Discord", "url", "https://discord.com/api/v10/gateway"),
    ("Discord CDN", "url", "https://cdn.discordapp.com/embed/avatars/0.png"),
]

# --------------------------------------------------------------------------
# low level helpers
# --------------------------------------------------------------------------
def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def pythonw_path() -> Path:
    p = Path(sys.executable).with_name("pythonw.exe")
    return p if p.exists() else Path(sys.executable)


def run_cmd(args, timeout=20, check=False):
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
            check=check,
        )
    except Exception as exc:
        class R:
            returncode = -1
            stdout = ""
            stderr = str(exc)
        return R()


def append_gui_log(message: str):
    try:
        with GUI_LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
    except Exception:
        pass


def read_text(path: Path, limit=4000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:
        return ""


def which_curl() -> str:
    p = shutil.which("curl")
    if p:
        return p
    p = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "curl.exe")
    return p if Path(p).exists() else "curl"


CURL = which_curl()

# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------
DEFAULT_SETTINGS = {
    "profile": "universal",
    "variant": "auto",
    "gameUdp": False,
    "dnsFix": True,
    "dnsName": "",
    "dnsPrimary": "",
    "dnsSecondary": "",
    "tunnels": True,
    "autoStart": False,
    "stopOnExit": False,
}


def load_settings():
    s = dict(DEFAULT_SETTINGS)
    try:
        if SETTINGS_FILE.exists():
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            for k in s:
                if k in data and data[k] is not None:
                    s[k] = data[k]
    except Exception:
        pass
    return s


def save_settings(s):
    try:
        SETTINGS_FILE.write_text(
            json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


# --------------------------------------------------------------------------
# Windows network API (adapters / DNS)
# --------------------------------------------------------------------------
class SOCKET_ADDRESS(ctypes.Structure):
    _fields_ = [
        ("lpSockaddr", ctypes.c_void_p),
        ("iSockaddrLength", ctypes.c_int),
    ]


class IP_ADAPTER_UNICAST_ADDRESS(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.ULONG),
        ("Flags", wintypes.DWORD),
        ("Next", ctypes.c_void_p),
        ("Address", SOCKET_ADDRESS),
    ]


class IP_ADAPTER_DNS_SERVER_ADDRESS(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.ULONG),
        ("Reserved", wintypes.DWORD),
        ("Next", ctypes.c_void_p),
        ("Address", SOCKET_ADDRESS),
    ]


class IP_ADAPTER_ADDRESSES(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.ULONG),
        ("IfIndex", wintypes.DWORD),
        ("Next", ctypes.c_void_p),
        ("AdapterName", ctypes.c_char_p),
        ("FirstUnicastAddress", ctypes.POINTER(IP_ADAPTER_UNICAST_ADDRESS)),
        ("FirstAnycastAddress", ctypes.c_void_p),
        ("FirstMulticastAddress", ctypes.c_void_p),
        ("FirstDnsServerAddress", ctypes.POINTER(IP_ADAPTER_DNS_SERVER_ADDRESS)),
        ("DnsSuffix", ctypes.c_wchar_p),
        ("Description", ctypes.c_wchar_p),
        ("FriendlyName", ctypes.c_wchar_p),
        ("PhysicalAddress", ctypes.c_ubyte * 8),
        ("PhysicalAddressLength", wintypes.ULONG),
        ("Flags", wintypes.ULONG),
        ("Mtu", wintypes.ULONG),
        ("IfType", wintypes.ULONG),
        ("OperStatus", ctypes.c_int),
    ]


def _ip_from_sockaddr(ptr):
    if not ptr:
        return None
    try:
        family = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_ushort)).contents.value
        if family != socket.AF_INET:
            return None
        raw = ctypes.string_at(ptr, 8)
        return socket.inet_ntoa(raw[4:8])
    except Exception:
        return None


def get_adapters():
    """Список IPv4-адаптеров через GetAdaptersAddresses (без PowerShell)."""
    size = wintypes.ULONG(32768)
    buf = ctypes.create_string_buffer(size.value)
    ret = ctypes.windll.iphlpapi.GetAdaptersAddresses(
        socket.AF_INET, 0, None, buf, ctypes.byref(size)
    )
    if ret:
        return []
    out = []
    p = ctypes.cast(buf, ctypes.POINTER(IP_ADAPTER_ADDRESSES))
    while p:
        a = p.contents
        ips, dns = [], []
        u = a.FirstUnicastAddress
        while u:
            ip = _ip_from_sockaddr(u.contents.Address.lpSockaddr)
            if ip:
                ips.append(ip)
            nxt = u.contents.Next
            u = (
                ctypes.cast(nxt, ctypes.POINTER(IP_ADAPTER_UNICAST_ADDRESS))
                if nxt
                else None
            )
        d = a.FirstDnsServerAddress
        while d:
            ip = _ip_from_sockaddr(d.contents.Address.lpSockaddr)
            if ip:
                dns.append(ip)
            nxt = d.contents.Next
            d = (
                ctypes.cast(nxt, ctypes.POINTER(IP_ADAPTER_DNS_SERVER_ADDRESS))
                if nxt
                else None
            )
        out.append(
            {
                "index": a.IfIndex,
                "name": a.FriendlyName or "",
                "desc": a.Description or "",
                "status": a.OperStatus,
                "dhcp": bool(a.Flags & 4),
                "ips": ips,
                "dns": dns,
            }
        )
        nxt = a.Next
        p = ctypes.cast(nxt, ctypes.POINTER(IP_ADAPTER_ADDRESSES)) if nxt else None
    return out


def get_best_adapter():
    """Адаптер, через который реально идёт интернет (GetBestInterface)."""
    try:
        best = wintypes.ULONG()
        addr = ctypes.c_ulong(int.from_bytes(socket.inet_aton("8.8.8.8"), "little"))
        if ctypes.windll.iphlpapi.GetBestInterface(addr, ctypes.byref(best)) == 0:
            for a in get_adapters():
                if a["index"] == best.value:
                    return a
    except Exception:
        pass
    candidates = [
        a
        for a in get_adapters()
        if a["status"] == 1
        and a["ips"]
        and not a["ips"][0].startswith(("127.", "169.254."))
        and not re.search(
            r"radmin|vpn|wireguard|openvpn|tap|tun|outline|bluetooth|loopback|teredo",
            (a["name"] + " " + a["desc"]).lower(),
        )
    ]
    return candidates[0] if candidates else None


def dns_query(server: str, name: str, timeout=2.0):
    """Сырой DNS-запрос A-записи к конкретному серверу. Без зависимостей."""
    try:
        tid = random.randint(0, 65535)
        q = struct.pack("!HHHHHH", tid, 0x0100, 1, 0, 0, 0)
        for part in name.rstrip(".").split("."):
            q += bytes([len(part)]) + part.encode("ascii")
        q += b"\x00" + struct.pack("!HH", 1, 1)

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        try:
            s.sendto(q, (server, 53))
            data, _ = s.recvfrom(4096)
        finally:
            s.close()

        if len(data) < 12:
            return []
        _, flags, qd, an, _, _ = struct.unpack("!HHHHHH", data[:12])
        if flags & 0x000F:
            return []
        idx = 12
        for _ in range(qd):
            while data[idx] != 0:
                idx += 1 + data[idx]
            idx += 5
        ips = []
        for _ in range(an):
            if idx >= len(data):
                break
            if data[idx] & 0xC0 == 0xC0:
                idx += 2
            else:
                while data[idx] != 0:
                    idx += 1 + data[idx]
                idx += 1
            if idx + 10 > len(data):
                break
            rtype, _rclass, _ttl, rdlen = struct.unpack("!HHIH", data[idx : idx + 10])
            idx += 10
            rdata = data[idx : idx + rdlen]
            idx += rdlen
            if rtype == 1 and rdlen == 4:
                ips.append(socket.inet_ntoa(rdata))
        return ips
    except Exception:
        return []


def flush_dns():
    run_cmd(["ipconfig", "/flushdns"], timeout=10)


def system_resolves(name="www.youtube.com"):
    try:
        socket.getaddrinfo(name, 80, socket.AF_INET, socket.SOCK_STREAM)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# DNS fix
# --------------------------------------------------------------------------
DNS_SERVERS = [
    ("Quad9", "9.9.9.9", "149.112.112.112"),
    ("Cloudflare", "1.1.1.1", "1.0.0.1"),
    ("Google", "8.8.8.8", "8.8.4.4"),
    ("Yandex", "77.88.8.8", "77.88.8.1"),
]
DNS_BY_NAME = {name: (primary, secondary) for name, primary, secondary in DNS_SERVERS}

DNS_HOSTS = [
    "www.youtube.com",
    "steamcommunity.com",
    "discord.com",
    "api.steampowered.com",
    "login.steampowered.com",
]

GOOGLE_PREFIXES = (
    "142.250.", "142.251.", "172.217.", "216.58.", "216.239.",
    "74.125.", "173.194.", "209.85.", "108.177.", "64.233.",
    "66.102.", "72.14.", "142.0.", "142.1.",
)


def is_google_ip(ip: str) -> bool:
    return ip.startswith(GOOGLE_PREFIXES)


def ensure_dns_backup(ad):
    if DNS_BACKUP_FILE.exists():
        return
    backup = {
        "ifIndex": ad["index"],
        "name": ad["name"],
        "dhcp": bool(ad["dhcp"]),
        "servers": list(ad["dns"]),
    }
    try:
        DNS_BACKUP_FILE.write_text(
            json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def apply_dns_pair(ad, primary: str, secondary: str):
    ensure_dns_backup(ad)
    name = ad["name"]
    run_cmd(["netsh", "interface", "ipv4", "set", "dns", f"name={name}", "static", primary, "primary"])
    if secondary and secondary != primary:
        run_cmd(["netsh", "interface", "ipv4", "add", "dns", f"name={name}", secondary, "index=2"])
    flush_dns()
    time.sleep(0.55)
    return system_resolves("www.youtube.com")


def probe_dns(primary: str, secondary: str):
    """Проверяет DNS-сервер по ключевым доменам. Возвращает score 0..5."""
    hosts = {}
    latency = 0.0
    for host in DNS_HOSTS:
        t0 = time.time()
        ips = dns_query(primary, host, timeout=2.5)
        if host == "www.youtube.com":
            good = any(is_google_ip(ip) for ip in ips)
            if not good and secondary:
                ips = dns_query(secondary, host, timeout=2.5)
                good = any(is_google_ip(ip) for ip in ips)
        else:
            good = bool(ips)
            if not good and secondary:
                ips = dns_query(secondary, host, timeout=2.5)
                good = bool(ips)
        latency += time.time() - t0
        hosts[host] = {"ok": good, "ips": ips}
    score = sum(1 for v in hosts.values() if v["ok"])
    return {"score": score, "latency": latency, "hosts": hosts}


def set_youtube_dns():
    """Ставит лучший рабочий DNS. Возвращает (ok, detail)."""
    ad = get_best_adapter()
    if not ad or not ad["name"]:
        return False, "не найден активный сетевой адаптер"

    s = load_settings()
    saved = (
        s.get("dnsName") or "Сохранённый",
        s.get("dnsPrimary") or "",
        s.get("dnsSecondary") or "",
    )
    allowed = {primary for _, primary, _ in DNS_SERVERS}
    custom = saved[0] == "Свой" and bool(saved[1])
    if custom:
        allowed.add(saved[1])
    if (saved[1] in allowed and saved[1] in ad["dns"]
            and system_resolves("www.youtube.com")
            and system_resolves("steamcommunity.com")):
        return True, f"DNS {saved[0]} уже активен · ключевые домены резолвятся"

    if custom:
        apply_dns_pair(ad, saved[1], saved[2])
        if system_resolves("www.youtube.com"):
            s["dnsFix"] = True
            save_settings(s)
            return True, f"DNS Свой · {saved[1]}" + (f" / {saved[2]}" if saved[2] else "")

    candidates = []
    if saved[1] in allowed:
        candidates.append(saved)
    candidates.extend(DNS_SERVERS)

    seen = set()
    uniq = []
    for name, primary, secondary in candidates:
        if primary and primary not in seen:
            uniq.append((name, primary, secondary))
            seen.add(primary)

    fallback = None
    for name, primary, secondary in uniq:
        probe = probe_dns(primary, secondary)
        if (probe["score"] >= 4
                and probe["hosts"]["www.youtube.com"]["ok"]
                and probe["hosts"]["steamcommunity.com"]["ok"]):
            apply_dns_pair(ad, primary, secondary)
            if system_resolves("www.youtube.com") and system_resolves("steamcommunity.com"):
                s["dnsFix"] = True
                s["dnsName"] = name
                s["dnsPrimary"] = primary
                s["dnsSecondary"] = secondary
                save_settings(s)
                return True, f"DNS {name} · {primary} / {secondary} · ключевые домены резолвятся"
        if fallback is None and probe["hosts"]["www.youtube.com"]["ok"]:
            fallback = (name, primary, secondary)

    if fallback:
        name, primary, secondary = fallback
        apply_dns_pair(ad, primary, secondary)
        s["dnsFix"] = True
        s["dnsName"] = name
        s["dnsPrimary"] = primary
        s["dnsSecondary"] = secondary
        save_settings(s)
        return True, f"DNS {name} · {primary} / {secondary} (частичная проверка)"

    return False, "ни один DNS-сервер не отдаёт youtube.com"


def dns_tune(progress=None):
    """Подбирает DNS: сначала российские, потом резервные. Проверяет и DNS, и связь."""
    ad = get_best_adapter()
    if not ad or not ad["name"]:
        return {"ok": False, "detail": "не найден активный сетевой адаптер", "results": []}

    results = []
    best = None
    best_score = -1
    best_checks = None

    for name, primary, secondary in DNS_SERVERS:
        if progress:
            progress(f"{name} ({primary})")
        probe = probe_dns(primary, secondary)
        row = {
            "name": name, "primary": primary, "secondary": secondary,
            "dns_score": probe["score"], "latency": probe["latency"],
            "url_ok": 0, "url_total": 0, "checks": [],
        }
        score = probe["score"] * 10
        if (probe["score"] >= 4
                and probe["hosts"]["www.youtube.com"]["ok"]
                and probe["hosts"]["steamcommunity.com"]["ok"]):
            apply_dns_pair(ad, primary, secondary)
            checks = connection_targets()
            row["url_ok"] = sum(1 for c in checks if c["ok"])
            row["url_total"] = len(checks)
            row["checks"] = checks
            score += row["url_ok"]
            if score > best_score:
                best_score = score
                best = (name, primary, secondary)
                best_checks = checks
            append_gui_log(
                f"DNS-TUNE {name} {primary}/{secondary} dns={probe['score']}/{len(DNS_HOSTS)} "
                f"url={row['url_ok']}/{row['url_total']}"
            )
            if row["url_ok"] == row["url_total"] and probe["score"] == len(DNS_HOSTS):
                results.append(row)
                break
        results.append(row)

    if best is None:
        for r in results:
            if r["dns_score"] > best_score:
                best_score = r["dns_score"]
                best = (r["name"], r["primary"], r["secondary"])

    if best is None:
        return {"ok": False, "detail": "ни один DNS не подошёл", "results": results}

    name, primary, secondary = best
    apply_dns_pair(ad, primary, secondary)
    s = load_settings()
    s["dnsFix"] = True
    s["dnsName"] = name
    s["dnsPrimary"] = primary
    s["dnsSecondary"] = secondary
    save_settings(s)

    checks = best_checks or connection_targets()
    ok = sum(1 for c in checks if c["ok"])
    return {
        "ok": True,
        "name": name,
        "primary": primary,
        "secondary": secondary,
        "results": results,
        "checks": checks,
        "url_ok": ok,
        "url_total": len(checks),
        "score": best_score,
    }


def restore_youtube_dns():
    if not DNS_BACKUP_FILE.exists():
        return True, "резервная копия DNS не найдена"
    try:
        b = json.loads(DNS_BACKUP_FILE.read_text(encoding="utf-8"))
        name = b.get("name")
        if b.get("dhcp") or not b.get("servers"):
            run_cmd(["netsh", "interface", "ipv4", "set", "dns", f"name={name}", "dhcp"])
        else:
            servers = b["servers"]
            run_cmd(["netsh", "interface", "ipv4", "set", "dns", f"name={name}", "static", servers[0], "primary"])
            for i, srv in enumerate(servers[1:], start=2):
                run_cmd(["netsh", "interface", "ipv4", "add", "dns", f"name={name}", srv, f"index={i}"])
        flush_dns()
        DNS_BACKUP_FILE.unlink(missing_ok=True)
        return True, "DNS возвращён как было"
    except Exception as exc:
        return False, f"не удалось вернуть DNS: {exc}"


# --------------------------------------------------------------------------
# tunnels / base fixes / legacy service
# --------------------------------------------------------------------------
def disable_tunnels():
    msgs = []
    for args in (
        ["netsh", "interface", "teredo", "set", "state", "disabled"],
        ["netsh", "interface", "isatap", "set", "state", "disabled"],
        ["netsh", "interface", "6to4", "set", "state", "disabled"],
    ):
        run_cmd(args, timeout=10)
    msgs.append("Teredo / ISATAP / 6to4 отключены")
    return msgs


def restore_tunnels():
    for args in (
        ["netsh", "interface", "teredo", "set", "state", "type=default"],
        ["netsh", "interface", "isatap", "set", "state", "default"],
        ["netsh", "interface", "6to4", "set", "state", "default"],
    ):
        run_cmd(args, timeout=10)


def base_fixes():
    msgs = []
    r = run_cmd(["sc", "query", "BFE"])
    out = (r.stdout or "") + (r.stderr or "")
    if "RUNNING" not in out.upper():
        run_cmd(["sc", "start", "BFE"], timeout=15)
        msgs.append("Служба BFE запущена")
    run_cmd(["netsh", "interface", "tcp", "set", "global", "timestamps=enabled"], timeout=10)
    return msgs


def disable_legacy_service():
    r = run_cmd(["sc", "query", "zapret"])
    txt = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0 or "1060" in txt:
        return "старая служба zapret не найдена"
    run_cmd(["sc", "stop", "zapret"], timeout=15)
    time.sleep(0.8)
    run_cmd(["sc", "config", "zapret", "start=", "demand"], timeout=10)
    return "старая служба zapret остановлена (автозапуск отключён)"


# --------------------------------------------------------------------------
# engine
# --------------------------------------------------------------------------
def hostlist_args():
    return [
        f"--hostlist={LISTS / 'list-general.txt'}",
        f"--hostlist={LISTS / 'list-general-user.txt'}",
        f"--hostlist={LISTS / 'list-steam.txt'}",
    ]


def exclude_args():
    return [
        f"--hostlist-exclude={LISTS / 'list-exclude.txt'}",
        f"--hostlist-exclude={LISTS / 'list-exclude-user.txt'}",
        f"--ipset-exclude={LISTS / 'ipset-exclude.txt'}",
        f"--ipset-exclude={LISTS / 'ipset-exclude-user.txt'}",
    ]


def google_rule(variant: str):
    base = [
        "--filter-tcp=443",
        f"--hostlist={LISTS / 'list-google.txt'}",
        "--ip-id=zero",
    ]
    pattern = BIN / "tls_clienthello_www_google_com.bin"
    if variant == "hostfakesplit":
        return base + [
            "--dpi-desync=hostfakesplit",
            "--dpi-desync-fooling=ts",
            "--dpi-desync-hostfakesplit-mod=host=www.google.com",
        ]
    if variant == "fakemultisplit":
        return base + [
            "--dpi-desync=fake,multisplit",
            "--dpi-desync-split-seqovl=681",
            "--dpi-desync-split-pos=1",
            "--dpi-desync-fooling=ts",
            "--dpi-desync-repeats=8",
            f"--dpi-desync-split-seqovl-pattern={pattern}",
            f"--dpi-desync-fake-tls={pattern}",
        ]
    if variant == "multisplit652":
        return base + [
            "--dpi-desync=multisplit",
            "--dpi-desync-split-seqovl=652",
            "--dpi-desync-split-pos=2",
            f"--dpi-desync-split-seqovl-pattern={pattern}",
        ]
    if variant == "fake":
        return base + [
            "--dpi-desync=fake",
            "--dpi-desync-repeats=6",
            "--dpi-desync-fooling=ts",
            f"--dpi-desync-fake-tls={pattern}",
        ]
    if variant == "multidisorder":
        return base + [
            "--dpi-desync=fake,multidisorder",
            "--dpi-desync-split-pos=1,midsld",
            "--dpi-desync-repeats=11",
            "--dpi-desync-fooling=badseq",
            f"--dpi-desync-fake-tls={pattern}",
            "--dpi-desync-fake-tls-mod=rnd,dupsid,sni=www.google.com",
        ]
    if variant == "disorder":
        return base + ["--dpi-desync=disorder", "--dpi-desync-split-pos=1"]
    if variant == "split2":
        return base + ["--dpi-desync=split2", "--dpi-desync-split-pos=2"]
    return google_rule("hostfakesplit")


def steam_api_rule():
    """Отдельный обход для api.steampowered.com (часто режется по SNI)."""
    return [
        "--filter-tcp=443",
        f"--hostlist={LISTS / 'list-steam-api.txt'}",
        "--dpi-desync=hostfakesplit",
        "--dpi-desync-fooling=ts",
        "--dpi-desync-hostfakesplit-mod=host=www.google.com",
    ]


def build_args(profile="universal", variant="hostfakesplit", game_udp=False):
    general = hostlist_args()
    excl = exclude_args()
    ipset = [f"--ipset={LISTS / 'ipset-all.txt'}"] + excl
    google = [f"--hostlist={LISTS / 'list-google.txt'}"]
    steam = [f"--hostlist={LISTS / 'list-steam.txt'}"]
    quic = BIN / "quic_initial_www_google_com.bin"
    discord = BIN / "ACTIVE_DISCORD_UDP.bin"
    game_udp_bin = BIN / "ACTIVE_GAME_UDP.bin"
    pattern = BIN / "tls_clienthello_www_google_com.bin"

    wf_tcp = "80,443,2053,2083,2087,2096,8443"
    wf_udp = "443,19294-19344,50000-50100"
    if game_udp:
        wf_tcp += ",27000-27100"
        wf_udp += ",27000-27100"

    rules = []
    is_universal = profile in ("universal", "max")
    is_youtube = profile == "youtube"
    is_discord = profile == "discord"
    is_steam = profile == "steam"

    if is_universal or is_discord:
        rules.append(
            ["--filter-udp=443"]
            + general
            + excl
            + ["--dpi-desync=fake", "--dpi-desync-repeats=6", f"--dpi-desync-fake-quic={quic}"]
        )
        rules.append(
            ["--filter-udp=19294-19344,50000-50100", "--filter-l7=discord,stun"]
            + ["--dpi-desync=fake", f"--dpi-desync-fake-discord={discord}", f"--dpi-desync-fake-stun={discord}", "--dpi-desync-repeats=6"]
        )
        rules.append(
            ["--filter-tcp=2053,2083,2087,2096,8443", "--hostlist-domains=discord.media"]
            + ["--dpi-desync=multisplit", "--dpi-desync-split-seqovl=681", "--dpi-desync-split-pos=1", f"--dpi-desync-split-seqovl-pattern={pattern}"]
        )
        rules.append(google_rule(variant))
        rules.append(steam_api_rule())
        rules.append(
            ["--filter-tcp=80,443"]
            + general
            + excl
            + ["--dpi-desync=multisplit", "--dpi-desync-split-seqovl=681", "--dpi-desync-split-pos=1", f"--dpi-desync-split-seqovl-pattern={pattern}"]
        )

    if is_youtube:
        rules.append(
            ["--filter-udp=443"]
            + google
            + ["--dpi-desync=fake", "--dpi-desync-repeats=6", f"--dpi-desync-fake-quic={quic}"]
        )
        rules.append(google_rule(variant))
        rules.append(
            ["--filter-tcp=80,443"]
            + google
            + ["--dpi-desync=multisplit", "--dpi-desync-split-seqovl=681", "--dpi-desync-split-pos=1", f"--dpi-desync-split-seqovl-pattern={pattern}"]
        )

    if is_steam:
        rules.append(
            ["--filter-udp=443"]
            + steam
            + ["--dpi-desync=fake", "--dpi-desync-repeats=6", f"--dpi-desync-fake-quic={quic}"]
        )
        rules.append(steam_api_rule())
        rules.append(
            ["--filter-tcp=80,443"]
            + steam
            + excl
            + ["--dpi-desync=multisplit", "--dpi-desync-split-seqovl=681", "--dpi-desync-split-pos=1", f"--dpi-desync-split-seqovl-pattern={pattern}"]
        )
        rules.append(
            ["--filter-tcp=443"]
            + general
            + excl
            + ["--dpi-desync=multisplit", "--dpi-desync-split-seqovl=681", "--dpi-desync-split-pos=1", f"--dpi-desync-split-seqovl-pattern={pattern}"]
        )

    if is_universal:
        rules.append(
            ["--filter-udp=443"]
            + google
            + ["--dpi-desync=fake", "--dpi-desync-repeats=6", f"--dpi-desync-fake-quic={quic}"]
        )
        rules.append(
            ["--filter-udp=443"]
            + ipset
            + ["--dpi-desync=fake", "--dpi-desync-repeats=6", f"--dpi-desync-fake-quic={quic}"]
        )
        rules.append(
            ["--filter-tcp=80,443,8443"]
            + ipset
            + ["--dpi-desync=multisplit", "--dpi-desync-split-seqovl=681", "--dpi-desync-split-pos=1", f"--dpi-desync-split-seqovl-pattern={pattern}"]
        )

    if game_udp and (is_universal or is_steam):
        rules.append(
            ["--filter-tcp=27000-27100"]
            + ["--dpi-desync=multisplit", "--dpi-desync-any-protocol=1", "--dpi-desync-cutoff=n3", "--dpi-desync-split-seqovl=681", "--dpi-desync-split-pos=1", f"--dpi-desync-split-seqovl-pattern={pattern}"]
        )
        rules.append(
            ["--filter-udp=27000-27100"]
            + ["--dpi-desync=fake", "--dpi-desync-repeats=4", "--dpi-desync-any-protocol=1", f"--dpi-desync-fake-unknown-udp={game_udp_bin}", "--dpi-desync-cutoff=n2"]
        )

    if not rules:
        rules.append(google_rule(variant))

    tokens = [f"--wf-tcp={wf_tcp}", f"--wf-udp={wf_udp}"]
    for i, rule in enumerate(rules):
        if i:
            tokens.append("--new")
        tokens.extend(rule)
    return tokens


def winws_pids():
    r = run_cmd(["tasklist", "/FI", "IMAGENAME eq winws.exe", "/FO", "CSV", "/NH"], timeout=10)
    pids = []
    for line in (r.stdout or "").splitlines():
        m = re.match(r'"winws\.exe","(\d+)"', line.strip(), re.I)
        if m:
            pids.append(int(m.group(1)))
    return pids


def engine_running() -> bool:
    return bool(winws_pids())


def stop_engine():
    run_cmd(["taskkill", "/F", "/IM", "winws.exe"], timeout=15)
    time.sleep(0.5)


def start_engine(profile, variant, game_udp):
    stop_engine()
    for p in (ENGINE_OUT, ENGINE_ERR):
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
    tokens = build_args(profile, variant, game_udp)
    append_gui_log("START profile=%s variant=%s gameUdp=%s" % (profile, variant, game_udp))
    out = ENGINE_OUT.open("wb")
    err = ENGINE_ERR.open("wb")
    try:
        proc = subprocess.Popen(
            [str(WINWS)] + tokens,
            cwd=str(BIN),
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            creationflags=CREATE_NO_WINDOW,
        )
    finally:
        out.close()
        err.close()
    time.sleep(1.5)
    if proc.poll() is not None:
        txt = (read_text(ENGINE_ERR) + "\n" + read_text(ENGINE_OUT)).strip()
        txt = re.sub(r"\s+", " ", txt)[:600]
        raise RuntimeError(f"winws завершился с кодом {proc.returncode}. {txt}")
    append_gui_log(f"ENGINE pid={proc.pid}")
    return proc.pid


# --------------------------------------------------------------------------
# http checks / auto tune
# --------------------------------------------------------------------------
def test_url(url, timeout=8, retries=2):
    started = time.time()
    last = (False, "ERR", "ошибка", 0.0)
    for attempt in range(max(1, retries)):
        try:
            r = subprocess.run(
                [
                    CURL, "-4", "-s", "-o", "NUL", "-w", "%{http_code} %{time_total}",
                    "-r", "0-0", "--connect-timeout", str(min(5, timeout)),
                    "--max-time", str(timeout), url,
                ],
                capture_output=True, text=True, errors="replace",
                timeout=timeout + 4, creationflags=CREATE_NO_WINDOW,
            )
            txt = (r.stdout or "").strip()
            code = "000"
            tm = 0.0
            m = re.match(r"^(\d{3})\s+([\d.,]+)", txt)
            if m:
                code = m.group(1)
                tm = float(m.group(2).replace(",", "."))
            ok = bool(re.match(r"^[23]\d\d$", code))
            detail = f"{code} · {tm:.2f} c" if ok else f"код {code}"
            last = (ok, code, detail, time.time() - started)
            if ok:
                return last
        except Exception as exc:
            last = (False, "ERR", f"ошибка: {exc}", time.time() - started)
        if attempt + 1 < retries:
            time.sleep(0.4)
    return last


def connection_targets():
    results = []
    for name, kind, value in CHECK_TARGETS:
        t0 = time.time()
        if kind == "dns":
            ok = system_resolves(value)
            results.append({
                "id": name, "ok": ok, "code": "A" if ok else "NX",
                "detail": ("резолвится за %.2f c" % (time.time() - t0)) if ok else "нет A-записи (DNS-блок)",
            })
        else:
            ok, code, detail, _ = test_url(value, 8)
            results.append({"id": name, "ok": ok, "code": code, "detail": detail})
    return results


def _tune_score(row):
    return (
        (100 if row["youtube"] else 0)
        + (50 if row["yt_video"] else 0)
        + (20 if row["yt_cdn"] else 0)
        + (20 if row["steam"] else 0)
        + (10 if row["steam_api"] else 0)
        + (10 if row["discord"] else 0)
        + (5 if row["discord_cdn"] else 0)
    )


def auto_tune(profile, game_udp, progress=None):
    results = []
    best, best_score = None, -1
    max_score = 215
    yt_home = "https://www.youtube.com"
    yt_video = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    yt_cdn = "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"
    steam = "https://steamcommunity.com"
    steam_api = "https://api.steampowered.com/ISteamWebAPIUtil/GetServerInfo/v1/"
    discord = "https://discord.com/api/v10/gateway"
    discord_cdn = "https://cdn.discordapp.com/embed/avatars/0.png"

    for variant in VARIANTS:
        if progress:
            progress(variant)
        row = {
            "variant": variant, "youtube": False, "yt_video": False,
            "yt_cdn": False, "steam": False, "steam_api": False,
            "discord": False, "discord_cdn": False, "score": 0, "code": "",
        }
        try:
            start_engine(profile, variant, game_udp)
            time.sleep(0.8)
            ok, code, _detail, _ = test_url(yt_home, 7)
            row["youtube"] = ok
            row["code"] = code
            if ok:
                row["yt_video"] = test_url(yt_video, 7)[0]
                row["yt_cdn"] = test_url(yt_cdn, 5)[0]
            row["steam"] = test_url(steam, 6)[0]
            row["steam_api"] = test_url(steam_api, 6)[0]
            row["discord"] = test_url(discord, 6)[0]
            row["discord_cdn"] = test_url(discord_cdn, 5)[0]
            row["score"] = _tune_score(row)
            if row["score"] > best_score:
                best_score = row["score"]
                best = variant
            results.append(row)
            append_gui_log(
                f"TUNE {variant} score={row['score']} yt={row['youtube']} "
                f"video={row['yt_video']} cdn={row['yt_cdn']} steam={row['steam']} "
                f"api={row['steam_api']} discord={row['discord']}"
            )
        except Exception as exc:
            row["code"] = "ERR"
            results.append(row)
            append_gui_log(f"TUNE {variant} error: {exc}")
        finally:
            stop_engine()
        if best_score >= max_score:
            break

    # повтор лучшего варианта убирает случайные единичные таймауты
    if best and best_score < max_score:
        row = next((r for r in results if r["variant"] == best), None)
        if row:
            if progress:
                progress(f"перепроверка {best}")
            try:
                start_engine(profile, best, game_udp)
                time.sleep(0.8)
                if not row["youtube"]:
                    ok, code, _d, _ = test_url(yt_home, 7)
                    row["youtube"] = ok
                    row["code"] = code
                if row["youtube"]:
                    if not row["yt_video"]:
                        row["yt_video"] = test_url(yt_video, 7)[0]
                    if not row["yt_cdn"]:
                        row["yt_cdn"] = test_url(yt_cdn, 5)[0]
                if not row["steam"]:
                    row["steam"] = test_url(steam, 6)[0]
                if not row["steam_api"]:
                    row["steam_api"] = test_url(steam_api, 6)[0]
                if not row["discord"]:
                    row["discord"] = test_url(discord, 6)[0]
                if not row["discord_cdn"]:
                    row["discord_cdn"] = test_url(discord_cdn, 5)[0]
                row["score"] = _tune_score(row)
                best_score = row["score"]
                append_gui_log(f"TUNE retest {best} score={best_score}")
            except Exception as exc:
                append_gui_log(f"TUNE retest {best} error: {exc}")
            finally:
                stop_engine()

    return best or "multidisorder", best_score, results


# --------------------------------------------------------------------------
# diagnostics
# --------------------------------------------------------------------------
def sc_state(name):
    r = run_cmd(["sc", "query", name])
    txt = ((r.stdout or "") + "\n" + (r.stderr or "")).upper()
    if "1060" in txt or "FAILED" in txt:
        return None
    for state in ("RUNNING", "STOPPED", "START_PENDING", "STOP_PENDING"):
        if state in txt:
            return state
    return "?"


def process_exists(name):
    image = name if name.lower().endswith(".exe") else name + ".exe"
    r = run_cmd(["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"], timeout=10)
    return "no tasks" not in (r.stdout or "").lower() and image.lower() in (r.stdout or "").lower()


def teredo_enabled():
    r = run_cmd(["netsh", "interface", "teredo", "show", "state"], timeout=10)
    txt = ((r.stdout or "") + " " + (r.stderr or "")).lower()
    if any(w in txt for w in ("disabled", "отключ", "выключ", "not configured", "не настроен")):
        return False
    return "2001:0:" in txt or "qualified" in txt or "client" in txt


def proxy_enabled():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as k:
            return bool(winreg.QueryValueEx(k, "ProxyEnable")[0])
    except Exception:
        return False


def diagnostics():
    rows = []

    def add(name, status, detail):
        rows.append({"name": name, "status": status, "detail": detail})

    add("Права администратора", "ok" if is_admin() else "bad",
        "есть" if is_admin() else "нужны для WinDivert")

    bfe = sc_state("BFE")
    add("Base Filtering Engine", "ok" if bfe == "RUNNING" else "bad", bfe or "не найдена")

    legacy = sc_state("zapret")
    if legacy:
        add("Старая служба zapret", "warn" if legacy == "RUNNING" else "ok", legacy)
    else:
        add("Старая служба zapret", "ok", "не установлена")

    pids = winws_pids()
    add("Движок winws", "ok" if pids else "warn",
        ("PID " + ", ".join(map(str, pids))) if pids else "не запущен")

    wd = sc_state("WinDivert")
    add("Драйвер WinDivert", "ok" if wd == "RUNNING" else "warn",
        wd or "не запущен (запустится с winws)")

    dns_ok = system_resolves("www.youtube.com")
    add("DNS youtube.com", "ok" if dns_ok else "bad",
        "резолвится" if dns_ok else "NXDOMAIN / подмена DNS")

    ter = teredo_enabled()
    add("Teredo / IPv6-туннель", "warn" if ter else "ok",
        "активен, обычно ломает YouTube/Steam" if ter else "отключён")

    add("Системный прокси", "warn" if proxy_enabled() else "ok",
        "включён — может мешать" if proxy_enabled() else "выключен")

    vpn = []
    for a in get_adapters():
        if a["status"] == 1 and re.search(
            r"radmin|vpn|wireguard|openvpn|tap|tun|outline",
            (a["name"] + " " + a["desc"]).lower(),
        ):
            vpn.append(a["name"])
    add("VPN-адаптеры", "warn" if vpn else "ok", ", ".join(vpn) if vpn else "нет активных")

    conflicts = [p for p in ("AdguardSvc", "Killer", "SmartByte", "TracSrvWrapper", "EPWD")
                 if process_exists(p)]
    add("Конфликтующие программы", "bad" if conflicts else "ok",
        ", ".join(conflicts) if conflicts else "не найдены")

    add("curl.exe", "ok" if Path(CURL).exists() or shutil.which("curl") else "warn",
        "есть" if Path(CURL).exists() or shutil.which("curl") else "нет — проверки будут медленнее")

    return rows


# --------------------------------------------------------------------------
# autostart
# --------------------------------------------------------------------------
TASK_NAME = "ZapretControlPy"


def autostart_exists():
    r = run_cmd(["schtasks", "/Query", "/TN", TASK_NAME], timeout=10)
    return r.returncode == 0


def set_autostart(enable: bool):
    try:
        if enable:
            if getattr(sys, "frozen", False):
                tr = subprocess.list2cmdline([str(Path(sys.executable).resolve()), "-autostart"])
            else:
                script = Path(__file__).resolve()
                tr = subprocess.list2cmdline([str(pythonw_path()), str(script), "-autostart"])
            r = run_cmd(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", tr,
                         "/SC", "ONLOGON", "/RL", "HIGHEST", "/F"], timeout=20)
        else:
            r = run_cmd(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], timeout=20)
        return r.returncode == 0, (r.stdout or r.stderr or "").strip()
    except Exception as exc:
        return False, str(exc)


# --------------------------------------------------------------------------
# tkinter widgets
# --------------------------------------------------------------------------
def round_rect(canvas, x1, y1, x2, y2, r, **kw):
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, splinesteps=36, **kw)


class RoundedButton(tk.Canvas):
    def __init__(self, parent, text, w, h, bg, fg, hover, command,
                 radius=9, font=None, border=None, parent_bg=None):
        pbg = parent_bg or parent.cget("bg")
        super().__init__(parent, width=w, height=h, bg=pbg,
                         highlightthickness=0, bd=0, takefocus=0)
        self.command = command
        self.enabled = True
        self.bg = bg
        self.fg = fg
        self.hover = hover
        self.shape = round_rect(self, 1, 1, w - 1, h - 1, radius,
                                fill=bg, outline=border or bg)
        self.label = self.create_text(w // 2, h // 2, text=text, fill=fg,
                                      font=font or (FONT, 10, "bold"))
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)

    def _click(self, _event=None):
        if self.enabled and self.command:
            self.command()

    def _enter(self, _event=None):
        if self.enabled:
            self.itemconfigure(self.shape, fill=self.hover)

    def _leave(self, _event=None):
        if self.enabled:
            self.itemconfigure(self.shape, fill=self.bg)

    def set_enabled(self, value):
        self.enabled = bool(value)
        self.itemconfigure(self.shape, fill=self.bg if value else CARD2)
        self.itemconfigure(self.label, fill=self.fg if value else DIM)

    def set_text(self, text):
        self.itemconfigure(self.label, text=text)


class ToggleSwitch(tk.Canvas):
    def __init__(self, parent, value, command, w=42, h=22):
        super().__init__(parent, width=w, height=h, bg=parent.cget("bg"),
                         highlightthickness=0, bd=0, takefocus=0)
        self.w, self.h = w, h
        self.value = bool(value)
        self.command = command
        self.track = round_rect(self, 1, 1, w - 1, h - 1, (h - 2) // 2,
                                fill=ACCENT if self.value else CARD2, outline="")
        self.knob = self.create_oval(0, 0, 1, 1, fill=WHITE, outline="")
        self._draw()
        self.bind("<Button-1>", self._click)

    def _draw(self):
        self.itemconfigure(self.track, fill=ACCENT if self.value else CARD2)
        pad = 3
        d = self.h - 2 * pad
        x2 = self.w - 3 if self.value else self.h - 1
        x1 = x2 - d
        self.coords(self.knob, x1, pad, x2, pad + d)

    def set_value(self, value):
        self.value = bool(value)
        self._draw()

    def _click(self, _event=None):
        self.value = not self.value
        self._draw()
        if self.command:
            self.command(self.value)


class ProfileCard(tk.Canvas):
    def __init__(self, parent, key, title, subtitle, command):
        super().__init__(parent, width=224, height=58, bg=CARD,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.key = key
        self.command = command
        self.selected = False
        self.rect = round_rect(self, 1, 1, 223, 57, 10, fill=CARD2, outline=BORDER)
        self.bar = self.create_rectangle(6, 12, 10, 46, fill=ACCENT, outline="")
        self.itemconfigure(self.bar, state="hidden")
        self.title = self.create_text(20, 19, text=title, anchor="w",
                                      fill=TEXT, font=(FONT, 10, "bold"))
        self.sub = self.create_text(20, 38, text=subtitle, anchor="w",
                                    fill=MUTED, font=(FONT, 8))
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)

    def _click(self, _event=None):
        self.command(self.key)

    def _enter(self, _event=None):
        if not self.selected:
            self.itemconfigure(self.rect, fill="#202531")

    def _leave(self, _event=None):
        if not self.selected:
            self.itemconfigure(self.rect, fill=CARD2)

    def set_selected(self, selected):
        self.selected = bool(selected)
        self.itemconfigure(
            self.rect,
            fill=CARD_SEL if selected else CARD2,
            outline="#1f3a31" if selected else BORDER,
        )
        self.itemconfigure(self.title, fill=ACCENT if selected else TEXT)
        self.itemconfigure(self.sub, fill="#aac8be" if selected else MUTED)
        self.itemconfigure(self.bar, state="normal" if selected else "hidden")


class CheckRow:
    def __init__(self, parent, y, name):
        self.frame = tk.Frame(parent, bg=CARD2, height=22)
        self.frame.place(x=10, y=y, width=692, height=22)
        self.dot = tk.Canvas(self.frame, width=12, height=12, bg=CARD2,
                             highlightthickness=0)
        self.dot.place(x=4, y=5)
        self.oval = self.dot.create_oval(2, 2, 10, 10, fill=DIM, outline="")
        self.name = tk.Label(self.frame, text=name, bg=CARD2, fg=TEXT,
                             font=(FONT, 9), anchor="w")
        self.name.place(x=24, y=0, width=190, height=22)
        self.detail = tk.Label(self.frame, text="—", bg=CARD2, fg=DIM,
                               font=(FONT, 8), anchor="w")
        self.detail.place(x=220, y=0, width=374, height=22)
        self.status = tk.Label(self.frame, text="—", bg=CARD2, fg=DIM,
                               font=(FONT, 8), anchor="e")
        self.status.place(x=600, y=0, width=84, height=22)

    def set(self, ok, detail=None, pending=False):
        if pending:
            color, text = WARN, "…"
        elif ok:
            color, text = ACCENT, "OK"
        else:
            color, text = DANGER, "FAIL"
        self.dot.itemconfigure(self.oval, fill=color)
        self.status.configure(text=text, fg=color)
        if detail is not None:
            self.detail.configure(text=detail)


class ServiceCard(tk.Canvas):
    def __init__(self, parent, key, title, x, y, w=168, h=132, subtitle="не проверено"):
        super().__init__(parent, width=w, height=h, bg=CARD2,
                         highlightthickness=0, bd=0)
        self.key = key
        self.rect = round_rect(self, 1, 1, w - 1, h - 1, 12,
                               fill="#171b24", outline=BORDER)
        self.dot = self.create_oval(18, 20, 30, 32, fill=DIM, outline="")
        self.title = self.create_text(40, 26, text=title, anchor="w",
                                      fill=TEXT, font=(FONT, 10, "bold"))
        self.status = self.create_text(18, 56, text="—", anchor="nw",
                                       fill=DIM, font=(FONT, 20, "bold"))
        self.detail = self.create_text(18, 94, text=subtitle, anchor="nw",
                                       fill=DIM, font=(FONT, 8), width=w - 30)
        self.place(x=x, y=y, width=w, height=h)

    def set(self, state, detail=None, status_text=None):
        colors = {"ok": ACCENT, "fail": DANGER, "pending": WARN, "idle": DIM}
        labels = {"ok": "OK", "fail": "FAIL", "pending": "…", "idle": "—"}
        color = colors.get(state, DIM)
        self.itemconfigure(self.dot, fill=color)
        self.itemconfigure(self.status, text=status_text or labels.get(state, "—"),
                           fill=color if state != "idle" else DIM)
        if detail is not None:
            self.itemconfigure(self.detail, text=detail,
                               fill=MUTED if state != "idle" else DIM)


def make_icon():
    """Простая иконка: зелёный круг с чёрной Z."""
    img = tk.PhotoImage(width=32, height=32)
    for y in range(32):
        for x in range(32):
            if (x - 15.5) ** 2 + (y - 15.5) ** 2 <= 15 ** 2:
                img.put(ACCENT, (x, y))
    z = ["11111", "00001", "00010", "00100", "01000", "10000", "11111"]
    ox, oy, cell = 8, 5, 3
    for ry, row in enumerate(z):
        for rx, ch in enumerate(row):
            if ch == "1":
                for dy in range(cell):
                    for dx in range(cell):
                        img.put(BG, (ox + rx * cell + dx, oy + ry * cell + dy))
    return img


# --------------------------------------------------------------------------
# main app
# --------------------------------------------------------------------------
class App:
    def __init__(self, root, preview=False, offscreen=False):
        self.root = root
        self.preview = preview
        self.offscreen = offscreen
        self.settings = load_settings()
        self.busy = False
        self.queue = queue.Queue()
        self.cards = {}
        self.toggles = {}
        self.log_pos = {"out": 0, "err": 0}
        self.last_visual = 0
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(250, self._startup)
        self.root.after(150, self._poll)

    # ------------------------------------------------------------------ ui
    def _build(self):
        self.root.title(f"Zapret Control v{APP_VERSION}")
        if self.offscreen:
            self.root.geometry("1040x720-2200-2200")
            try:
                self.root.attributes("-toolwindow", True)
            except Exception:
                pass
        else:
            self.root.geometry("1040x720")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)
        try:
            self.icon_img = make_icon()
            self.root.iconphoto(True, self.icon_img)
        except Exception:
            pass
        try:
            self.root.tk.call("tk", "scaling", 1.25)
        except Exception:
            pass

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Z.Horizontal.TProgressbar",
                        troughcolor=BG2, background=ACCENT,
                        bordercolor=BG2, lightcolor=ACCENT,
                        darkcolor=ACCENT)
        style.configure("Z.Vertical.TScrollbar",
                        background=CARD2, troughcolor=BG2,
                        bordercolor=BG2, arrowcolor=MUTED,
                        lightcolor=CARD2, darkcolor=CARD2)
        style.configure("Z.TCombobox",
                        fieldbackground=CARD2, background=CARD2, foreground=TEXT,
                        arrowcolor=MUTED, bordercolor=BORDER,
                        lightcolor=CARD2, darkcolor=CARD2, padding=2,
                        font=(FONT, 9))
        style.map("Z.TCombobox",
                  fieldbackground=[("readonly", CARD2)],
                  foreground=[("readonly", TEXT)],
                  background=[("readonly", CARD2)],
                  selectbackground=[("readonly", CARD_SEL)],
                  selectforeground=[("readonly", TEXT)])
        self.root.option_add("*TCombobox*Listbox.background", CARD2)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", CARD_SEL)
        self.root.option_add("*TCombobox*Listbox.selectForeground", TEXT)

        self._build_header()
        self._build_sidebar()
        self._build_main()
        self._build_statusbar()

        for key, card in self.cards.items():
            card.set_selected(key == self.settings["profile"])

    def _build_header(self):
        header = tk.Frame(self.root, bg=BG)
        header.place(x=16, y=8, width=1008, height=56)

        logo = tk.Canvas(header, width=44, height=44, bg=BG, highlightthickness=0)
        logo.place(x=0, y=4)
        logo.create_oval(2, 2, 42, 42, fill=ACCENT, outline=ACCENT)
        logo.create_text(22, 22, text="Z", fill=BG, font=(FONT, 20, "bold"))

        tk.Label(header, text="ZAPRET CONTROL", bg=BG, fg=TEXT,
                 font=(FONT, 15, "bold"), anchor="w").place(x=56, y=2, width=420, height=28)
        tk.Label(header, text="обход DPI  ·  YouTube  ·  Discord  ·  Steam",
                 bg=BG, fg=MUTED, font=(FONT, 9), anchor="w").place(x=57, y=30, width=460, height=20)

        tk.Label(header, text=f"v{APP_VERSION}", bg=BG, fg=DIM, font=(FONT, 8),
                 anchor="e").place(x=944, y=18, width=64, height=24)

    def _build_sidebar(self):
        side = tk.Canvas(self.root, width=252, height=604, bg=BG,
                         highlightthickness=0, bd=0)
        side.place(x=16, y=76)
        round_rect(side, 1, 1, 251, 603, 14, fill=CARD, outline=BORDER)
        self.sidebar = side

        tk.Label(side, text="ПРОФИЛЬ", bg=CARD, fg=DIM, font=(FONT, 8, "bold"),
                 anchor="w").place(x=18, y=12, width=200, height=18)

        for i, (key, (title, subtitle)) in enumerate(PROFILES.items()):
            card = ProfileCard(side, key, title, subtitle, self.select_profile)
            card.place(x=14, y=36 + i * 64, width=224, height=58)
            self.cards[key] = card

        tk.Label(side, text="НАСТРОЙКИ", bg=CARD, fg=DIM, font=(FONT, 8, "bold"),
                 anchor="w").place(x=18, y=368, width=200, height=18)
        tk.Frame(side, bg=BORDER, height=1).place(x=18, y=390, width=216, height=1)

        toggle_defs = [
            ("dnsFix", "DNS-фикс YouTube", self.on_dns_toggle),
            ("tunnels", "Убрать Teredo / IPv6", self.on_tunnels_toggle),
            ("gameUdp", "Игровой UDP (пинг!)", self.on_gameudp_toggle),
            ("autoStart", "Автозапуск с Windows", self.on_autostart_toggle),
            ("stopOnExit", "Стоп при выходе", self.on_stopexit_toggle),
        ]
        for i, (key, text, callback) in enumerate(toggle_defs):
            row = tk.Frame(side, bg=CARD, height=30)
            row.place(x=14, y=402 + i * 33, width=224, height=30)
            lbl = tk.Label(row, text=text, bg=CARD, fg=TEXT,
                           font=(FONT, 9, "bold"), anchor="w")
            lbl.place(x=0, y=5, width=176, height=20)
            sw = ToggleSwitch(row, bool(self.settings.get(key)), callback)
            sw.place(x=180, y=4, width=42, height=22)
            lbl.bind("<Button-1>", lambda e, s=sw: s._click())
            row.bind("<Button-1>", lambda e, s=sw: s._click())
            self.toggles[key] = sw

        self.btn_lists = RoundedButton(side, "Открыть списки", 104, 30, CARD2, TEXT, HOVER,
                                       lambda: self.open_path(LISTS), radius=7,
                                       font=(FONT, 8), parent_bg=CARD)
        self.btn_lists.place(x=14, y=570)
        self.btn_folder = RoundedButton(side, "Папка zapret", 106, 30, CARD2, TEXT, HOVER,
                                        lambda: self.open_path(APP_ROOT), radius=7,
                                        font=(FONT, 8), parent_bg=CARD)
        self.btn_folder.place(x=126, y=570)

    def _build_main(self):
        main = tk.Canvas(self.root, width=740, height=604, bg=BG,
                         highlightthickness=0, bd=0)
        main.place(x=284, y=76)
        round_rect(main, 1, 1, 739, 603, 14, fill=CARD, outline=BORDER)
        self.main = main

        # status card
        card = tk.Canvas(main, width=712, height=142, bg=CARD,
                         highlightthickness=0, bd=0)
        card.place(x=14, y=14)
        round_rect(card, 1, 1, 711, 141, 12, fill=CARD2, outline=BORDER)
        self.status_card = card
        self.status_bar_item = card.create_rectangle(
            7, 14, 11, 128, fill=MUTED, outline=""
        )

        self.status_dot = tk.Canvas(card, width=24, height=24, bg=CARD2, highlightthickness=0)
        self.status_dot.place(x=22, y=28)
        self.status_oval = self.status_dot.create_oval(3, 3, 21, 21, fill=MUTED, outline="")

        self.status_title = tk.Label(card, text="ОСТАНОВЛЕН", bg=CARD2, fg=TEXT,
                                     font=(FONT, 20, "bold"), anchor="w")
        self.status_title.place(x=54, y=18, width=440, height=38)
        self.status_sub = tk.Label(card, text="Движок не запущен", bg=CARD2, fg=MUTED,
                                   font=(FONT, 9), anchor="w")
        self.status_sub.place(x=56, y=58, width=440, height=20)
        self.status_info = tk.Label(card, text="", bg=CARD2, fg=DIM,
                                    font=(FONT, 8), anchor="w")
        self.status_info.place(x=56, y=84, width=440, height=18)

        tk.Label(card, text="DNS", bg=CARD2, fg=DIM, font=(FONT, 8),
                 anchor="w").place(x=56, y=106, width=36, height=22)
        self.dns_var = tk.StringVar(value="Авто")
        self.dns_combo = ttk.Combobox(
            card, textvariable=self.dns_var, state="readonly",
            style="Z.TCombobox",
            values=["Авто"] + list(DNS_BY_NAME.keys()) + ["Свой", "Системный (сброс)"],
        )
        self.dns_combo.place(x=96, y=104, width=160, height=26)
        self.dns_combo.bind("<<ComboboxSelected>>", self.on_dns_selected)

        self.btn_start = RoundedButton(card, "ЗАПУСТИТЬ", 184, 44, ACCENT, BG, ACCENT_DARK,
                                       self.start_ui, radius=11, font=(FONT, 10, "bold"),
                                       parent_bg=CARD2)
        self.btn_start.place(x=512, y=20)
        self.btn_stop = RoundedButton(card, "ОСТАНОВИТЬ", 184, 36, CARD, TEXT, HOVER,
                                      self.stop_ui, radius=9, font=(FONT, 9),
                                      parent_bg=CARD2)
        self.btn_stop.place(x=512, y=72)
        self.pbar = ttk.Progressbar(card, style="Z.Horizontal.TProgressbar",
                                    mode="indeterminate", length=184)
        self.pbar.place(x=512, y=118, width=184, height=5)
        self.pbar.place_forget()

        # actions: 4 equal buttons
        def action(text, x, command):
            b = RoundedButton(main, text, 172, 36, CARD2, TEXT, HOVER, command,
                              radius=9, font=(FONT, 8, "bold"), parent_bg=CARD)
            b.place(x=x, y=166)
            return b

        self.btn_tune = action("АВТОПОДБОР", 14, self.start_tune_ui)
        self.btn_dns = action("DNS-ПОДБОР", 194, self.start_dns_tune_ui)
        self.btn_test = action("ПРОВЕРКА", 374, self.start_test_ui)
        self.btn_diag = action("ДИАГНОСТИКА", 554, self.start_diag_ui)

        # services
        services = tk.Canvas(main, width=712, height=190, bg=CARD,
                             highlightthickness=0, bd=0)
        services.place(x=14, y=214)
        round_rect(services, 1, 1, 711, 189, 12, fill=CARD2, outline=BORDER)
        self.checks_frame = services
        tk.Label(services, text="СЕРВИСЫ", bg=CARD2, fg=DIM,
                 font=(FONT, 8, "bold"), anchor="w").place(x=18, y=10, width=300, height=18)

        self.service_cards = {}
        dns_name = self.settings.get("dnsName") or "не проверено"
        cards = [
            ("dns", "DNS", dns_name),
            ("youtube", "YouTube", "web + CDN"),
            ("steam", "Steam", "store · community · help"),
            ("discord", "Discord", "api · cdn"),
        ]
        for i, (key, title, subtitle) in enumerate(cards):
            card = ServiceCard(services, key, title, 2 + i * 180, 36,
                               w=168, h=132, subtitle=subtitle)
            self.service_cards[key] = card

        # log
        self.log_card = tk.Canvas(main, width=712, height=176, bg=CARD,
                                  highlightthickness=0, bd=0)
        self.log_card.place(x=14, y=414)
        round_rect(self.log_card, 1, 1, 711, 175, 12, fill=CARD2, outline=BORDER)
        tk.Label(self.log_card, text="ЛОГ", bg=CARD2, fg=DIM,
                 font=(FONT, 8, "bold"), anchor="w").place(x=18, y=8, width=200, height=18)
        self.btn_clear = RoundedButton(self.log_card, "очистить", 74, 22, CARD, MUTED, HOVER,
                                       self.clear_log, radius=6, font=(FONT, 8),
                                       parent_bg=CARD2)
        self.btn_clear.place(x=620, y=6)

        self.log = tk.Text(self.log_card, bg=BG2, fg="#9aa3b2", bd=0,
                           highlightthickness=0, font=(MONO, 9), wrap="word",
                           padx=6, pady=4, state="disabled")
        self.log.place(x=14, y=34, width=682, height=130)
        self.log.tag_configure("dim", foreground=DIM)
        self.log.tag_configure("text", foreground="#b4bcc9")
        self.log.tag_configure("ok", foreground=ACCENT)
        self.log.tag_configure("warn", foreground=WARN)
        self.log.tag_configure("err", foreground=DANGER)

    def _build_statusbar(self):
        bar = tk.Frame(self.root, bg=BG2, height=28)
        bar.place(x=0, y=692, width=1040, height=28)
        self.statusbar = tk.Label(bar, text="Готов", bg=BG2, fg=MUTED,
                                  font=(FONT, 8), anchor="w")
        self.statusbar.place(x=16, y=4, width=800, height=20)
        tk.Label(bar, text=f"zapret · WinDivert · v{APP_VERSION}", bg=BG2, fg=DIM,
                 font=(FONT, 8), anchor="e").place(x=820, y=4, width=204, height=20)

    # --------------------------------------------------------------- helpers
    def log_write(self, message, tag="text"):
        if not message:
            return
        append_gui_log(message)
        self.log.configure(state="normal")
        self.log.insert("end", f"[{datetime.now():%H:%M:%S}] ", "dim")
        self.log.insert("end", message.rstrip() + "\n", tag)
        self.log.see("end")
        try:
            lines = int(self.log.index("end-1c").split(".")[0])
            if lines > 650:
                self.log.delete("1.0", "200.0")
        except Exception:
            pass
        self.log.configure(state="disabled")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def open_path(self, path):
        try:
            os.startfile(str(path))
        except Exception as exc:
            self.log_write(f"Не удалось открыть папку: {exc}", "err")

    def set_statusbar(self, text):
        self.statusbar.configure(text=text)

    def set_status(self, state, title, subtitle="", info=""):
        colors = {
            "on": ACCENT,
            "off": MUTED,
            "busy": WARN,
            "err": DANGER,
        }
        color = colors.get(state, MUTED)
        self.status_dot.itemconfigure(self.status_oval, fill=color)
        self.status_card.itemconfigure(self.status_bar_item, fill=color)
        self.status_title.configure(text=title)
        self.status_sub.configure(text=subtitle)
        self.status_info.configure(text=info)

    def set_busy(self, busy, title=None):
        self.busy = bool(busy)
        for btn in (self.btn_start, self.btn_tune, self.btn_dns, self.btn_test, self.btn_diag, self.btn_lists, self.btn_folder):
            btn.set_enabled(not busy)
        if busy:
            self.btn_stop.set_enabled(False)
            self.pbar.place(x=512, y=118, width=184, height=5)
            self.pbar.start(14)
            self.set_status("busy", title or "ПОДОЖДИТЕ…", "Пожалуйста, подождите…")
        else:
            self.pbar.stop()
            self.pbar.place_forget()
            self.btn_stop.set_enabled(engine_running())

    def selected_profile_title(self):
        return PROFILES.get(self.settings["profile"], (self.settings["profile"], ""))[0]

    def variant_title(self, key=None):
        key = key or self.settings.get("variant", "auto")
        return VARIANT_TITLES.get(key, key)

    # --------------------------------------------------------------- startup
    def _startup(self):
        set_dark_titlebar(self.root)
        try:
            actual = autostart_exists()
            self.settings["autoStart"] = actual
            if "autoStart" in self.toggles:
                self.toggles["autoStart"].set_value(actual)
        except Exception:
            pass
        for key, card in self.cards.items():
            card.set_selected(key == self.settings["profile"])
        self.clear_checks()
        self.update_engine_visual()
        self._sync_dns_selector()
        self.log_write("Zapret Control запущен")
        self.log_write("Готов. Нажми «ПРОВЕРКА», чтобы обновить статусы сервисов.")
        if self.preview:
            self.root.after(5000, self._preview_close)
            return
        if AUTOSTART:
            self.root.after(1200, self.root.iconify)
        self.start_ui()

    def _preview_close(self):
        self.root.destroy()

    # --------------------------------------------------------------- network
    def prepare_network(self):
        self.log_write("--- подготовка сети ---")
        self.log_write(disable_legacy_service())
        for msg in base_fixes():
            self.log_write(msg)
        if self.settings.get("tunnels", True):
            for msg in disable_tunnels():
                self.log_write(msg)
        if self.settings.get("dnsFix", True):
            ok, detail = set_youtube_dns()
            self.log_write(detail, "ok" if ok else "warn")
            if not ok:
                self.settings["dnsFix"] = False
                save_settings(self.settings)
                if "dnsFix" in self.toggles:
                    self.toggles["dnsFix"].set_value(False)
                self.set_statusbar("DNS: " + detail)
                return
        elif DNS_BACKUP_FILE.exists():
            ok, detail = restore_youtube_dns()
            self.log_write(detail)
        self.settings = load_settings()
        self._sync_dns_selector()
        dns_name = self.settings.get("dnsName") or "авто"
        self.set_statusbar(
            "Профиль: %s · Teredo %s · DNS %s (%s)"
            % (
                self.selected_profile_title(),
                "off" if self.settings.get("tunnels", True) else "on",
                dns_name,
                "OK" if system_resolves("www.youtube.com") else "FAIL",
            )
        )

    # --------------------------------------------------------------- engine
    def update_engine_visual(self):
        pids = winws_pids()
        running = bool(pids)
        if self.busy:
            return
        if running:
            self.set_status(
                "on",
                "РАБОТАЕТ",
                "Профиль: %s  ·  стратегия: %s"
                % (self.selected_profile_title(), self.variant_title()),
                "PID %s  ·  WinDivert активен" % ", ".join(map(str, pids)),
            )
            self.btn_start.set_text("ПЕРЕЗАПУСТИТЬ")
            self.btn_start.set_enabled(True)
            self.btn_stop.set_enabled(True)
        else:
            self.set_status("off", "ОСТАНОВЛЕН", "Движок не запущен")
            self.btn_start.set_text("ЗАПУСТИТЬ")
            self.btn_start.set_enabled(True)
            self.btn_stop.set_enabled(False)

    def start_ui(self):
        if self.busy:
            return
        if not self.preview:
            self.set_busy(True, "ПОДГОТОВКА…")
            self.root.update_idletasks()
            try:
                self.prepare_network()
            finally:
                self.set_busy(False)
        variant = self.settings.get("variant") or "auto"
        if variant == "auto":
            self.start_tune_ui(skip_prepare=True)
            return
        self.set_busy(True, "ЗАПУСК…")
        try:
            pid = start_engine(self.settings["profile"], variant, bool(self.settings.get("gameUdp")))
            self.log_write(f"Движок запущен · PID {pid}", "ok")
        except Exception as exc:
            self.log_write(f"Ошибка запуска: {exc}", "err")
            messagebox.showerror("Zapret Control", str(exc), parent=self.root)
        finally:
            self.set_busy(False)
            self.update_engine_visual()

    def stop_ui(self):
        if self.busy:
            return
        self.set_busy(True, "ОСТАНОВКА…")
        try:
            stop_engine()
            self.log_write("Движок остановлен")
        finally:
            self.set_busy(False)
            self.update_engine_visual()

    def restart_engine(self):
        if self.busy:
            return
        variant = self.settings.get("variant") or "auto"
        if variant == "auto":
            self.start_tune_ui(skip_prepare=True)
            return
        self.set_busy(True, "ПЕРЕЗАПУСК…")
        try:
            start_engine(self.settings["profile"], variant, bool(self.settings.get("gameUdp")))
            self.log_write("Движок перезапущен", "ok")
        except Exception as exc:
            self.log_write(f"Ошибка перезапуска: {exc}", "err")
            messagebox.showerror("Zapret Control", str(exc), parent=self.root)
        finally:
            self.set_busy(False)
            self.update_engine_visual()

    # --------------------------------------------------------------- tune
    def start_tune_ui(self, skip_prepare=False):
        if self.busy:
            return
        if not skip_prepare and not self.preview:
            self.set_busy(True, "ПОДГОТОВКА…")
            self.root.update_idletasks()
            try:
                self.prepare_network()
            finally:
                self.set_busy(False)
        self.set_busy(True, "ПОДБОР СТРАТЕГИИ…")
        for key in ("youtube", "steam", "discord"):
            self.service_cards[key].set("pending", "подбор…")
        self.log_write("Автоподбор: перебираю стратегии…", "warn")
        threading.Thread(target=self._tune_worker, daemon=True).start()

    def _tune_worker(self):
        try:
            best, score, rows = auto_tune(
                self.settings["profile"],
                bool(self.settings.get("gameUdp")),
                lambda v: self.queue.put(("tune_progress", v)),
            )
            self.queue.put(("tune_done", best, score, rows))
        except Exception as exc:
            self.queue.put(("tune_error", str(exc)))

    def _finish_tune(self, best, score, rows):
        for r in rows:
            mark = "OK" if r["score"] >= 170 else "--"
            self.log_write(
                "  [%s] %-14s YT:%s/%s/%s Steam:%s/%s Discord:%s score=%s"
                % (mark, r["variant"], r["youtube"], r["yt_video"], r["yt_cdn"],
                   r["steam"], r["steam_api"], r["discord"], r["score"])
            )
        self.settings["variant"] = best
        save_settings(self.settings)
        self.log_write(f"Автоподбор: выбрана стратегия {best} (score {score})", "ok")
        if score < 100:
            self.log_write(
                "ВНИМАНИЕ: YouTube не открылся ни с одной стратегией. "
                "Проверьте DNS/VPN или включите другой профиль.",
                "warn",
            )
        elif score < 215:
            self.log_write(
                "Не все проверки идеальны — смотри строки выше, где стоит --.",
                "warn",
            )
        best_row = next((r for r in rows if r["variant"] == best), None)
        if best_row:
            yt_ok = best_row["youtube"] and best_row["yt_video"]
            st_ok = best_row["steam"] and best_row["steam_api"]
            dc_ok = best_row["discord"] and best_row["discord_cdn"]
            self.service_cards["youtube"].set("ok" if yt_ok else "fail", "автоподбор")
            self.service_cards["steam"].set("ok" if st_ok else "fail", "автоподбор")
            self.service_cards["discord"].set("ok" if dc_ok else "fail", "автоподбор")
        self.set_busy(True, "ЗАПУСК…")
        try:
            pid = start_engine(self.settings["profile"], best, bool(self.settings.get("gameUdp")))
            self.log_write(f"Движок запущен · PID {pid}", "ok")
        except Exception as exc:
            self.log_write(f"Ошибка запуска: {exc}", "err")
            messagebox.showerror("Zapret Control", str(exc), parent=self.root)
        finally:
            self.set_busy(False)
            self.update_engine_visual()

    # --------------------------------------------------------------- tests
    def clear_checks(self):
        self.service_cards["dns"].set("idle", self.settings.get("dnsName") or "не проверено")
        self.service_cards["youtube"].set("idle", "web + CDN")
        self.service_cards["steam"].set("idle", "store · community · help")
        self.service_cards["discord"].set("idle", "api · cdn")

    def start_test_ui(self):
        if self.busy:
            return
        self.set_busy(True, "ПРОВЕРКА СВЯЗИ…")
        for card in self.service_cards.values():
            card.set("pending", "проверяю…")
        self.log_write("--- проверка соединения ---")
        threading.Thread(target=self._test_worker, daemon=True).start()

    def _test_worker(self):
        try:
            self.queue.put(("test_done", connection_targets()))
        except Exception as exc:
            self.queue.put(("test_error", str(exc)))

    def update_service_cards(self, results):
        if not results:
            return

        def item(i):
            return results[i] if i < len(results) else None

        dns = item(0)
        if dns:
            self.service_cards["dns"].set("ok" if dns["ok"] else "fail", dns["detail"])

        def group(key, indexes, ok_detail):
            items = [item(i) for i in indexes]
            items = [x for x in items if x]
            if not items:
                return
            ok = all(x["ok"] for x in items)
            detail = ok_detail if ok else next(
                (x["detail"] for x in items if not x["ok"]), ok_detail
            )
            self.service_cards[key].set("ok" if ok else "fail", detail)

        group("youtube", (1, 2), "web + CDN")
        group("steam", (3, 4, 5), "store · community · help")
        group("discord", (6, 7), "api · cdn")

    def _finish_test(self, results):
        ok_count = 0
        self.update_service_cards(results)
        for r in results:
            if r["ok"]:
                ok_count += 1
            self.log_write("  %-16s %-5s %s" % (r["id"], "OK" if r["ok"] else "FAIL", r["detail"]),
                           "ok" if r["ok"] else "err")
        self.set_statusbar(f"Проверка: {ok_count} из {len(results)} OK")
        self.set_busy(False)
        self.update_engine_visual()

    # --------------------------------------------------------------- dns
    def start_dns_tune_ui(self):
        if self.busy:
            return
        self.set_busy(True, "ПОДБОР DNS…")
        self.log_write("Подбор DNS: проверяю российские и резервные серверы…", "warn")
        threading.Thread(target=self._dns_worker, daemon=True).start()

    def _dns_worker(self):
        try:
            res = dns_tune(lambda name: self.queue.put(("dns_progress", name)))
            self.queue.put(("dns_done", res))
        except Exception as exc:
            self.queue.put(("dns_error", str(exc)))

    def _finish_dns(self, res):
        if not res.get("ok"):
            detail = res.get("detail") or "не удалось подобрать DNS"
            self.log_write("DNS: " + detail, "err")
            self.set_busy(False)
            self.update_engine_visual()
            messagebox.showerror("Zapret Control", "DNS не подобран:\n" + detail, parent=self.root)
            return

        for r in res.get("results", []):
            self.log_write(
                "  %-12s %-15s DNS %s/%s · URL %s/%s"
                % (r["name"], r["primary"], r["dns_score"], len(DNS_HOSTS), r["url_ok"], r["url_total"])
            )
        self.log_write(
            "DNS выбран: %s · %s / %s" % (res["name"], res["primary"], res["secondary"]), "ok"
        )
        self.settings = load_settings()
        self._sync_dns_selector()
        if "dnsFix" in self.toggles:
            self.toggles["dnsFix"].set_value(True)
        self.update_service_cards(res.get("checks", []))
        if not res.get("checks"):
            self.service_cards["dns"].set("ok", f"{res['name']} · {res['primary']}")
        self.set_statusbar(
            "DNS: %s · %s / %s · проверки %s/%s"
            % (res["name"], res["primary"], res["secondary"], res["url_ok"], res["url_total"])
        )
        self.set_busy(False)
        self.update_engine_visual()
        messagebox.showinfo(
            "Zapret Control",
            f"DNS: {res['name']}\n{res['primary']} / {res['secondary']}\n\n"
            f"Проверки: {res['url_ok']} из {res['url_total']} OK",
            parent=self.root,
        )

    # --------------------------------------------------------------- diag
    def start_diag_ui(self):
        if self.busy:
            return
        self.set_busy(True, "ДИАГНОСТИКА…")
        threading.Thread(target=self._diag_worker, daemon=True).start()

    def _diag_worker(self):
        try:
            self.queue.put(("diag_done", diagnostics()))
        except Exception as exc:
            self.queue.put(("diag_error", str(exc)))

    def show_diagnostics(self, rows):
        dlg = tk.Toplevel(self.root)
        dlg.title("Диагностика")
        dlg.configure(bg=BG)
        dlg.geometry("620x480")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        try:
            set_dark_titlebar(dlg)
        except Exception:
            pass

        tk.Label(dlg, text="СОСТОЯНИЕ СИСТЕМЫ", bg=BG, fg=TEXT,
                 font=(FONT, 12, "bold"), anchor="w").place(x=20, y=16, width=400, height=26)
        tk.Label(dlg, text="красное — исправить, жёлтое — обратить внимание",
                 bg=BG, fg=MUTED, font=(FONT, 8), anchor="w").place(x=20, y=42, width=560, height=18)

        y = 74
        for r in rows:
            color = {"ok": ACCENT, "warn": WARN, "bad": DANGER}.get(r["status"], MUTED)
            dot = tk.Canvas(dlg, width=12, height=12, bg=BG, highlightthickness=0)
            dot.place(x=22, y=y + 4)
            dot.create_oval(2, 2, 10, 10, fill=color, outline="")
            tk.Label(dlg, text=r["name"], bg=BG, fg=TEXT, font=(FONT, 9),
                     anchor="w").place(x=42, y=y, width=230, height=20)
            tk.Label(dlg, text=r["detail"], bg=BG, fg=MUTED, font=(FONT, 8),
                     anchor="e").place(x=270, y=y, width=330, height=20)
            y += 24

        def fix_and_close():
            dlg.grab_release()
            dlg.destroy()
            self.prepare_network()
            self.update_engine_visual()

        RoundedButton(dlg, "ИСПРАВИТЬ СЕТЬ", 180, 36, ACCENT, BG, ACCENT_DARK,
                      fix_and_close, radius=8, font=(FONT, 9, "bold"),
                      parent_bg=BG).place(x=20, y=420)
        RoundedButton(dlg, "ЗАКРЫТЬ", 100, 36, CARD2, TEXT, HOVER,
                      lambda: (dlg.grab_release(), dlg.destroy()),
                      radius=8, font=(FONT, 9), parent_bg=BG).place(x=500, y=420)

    # --------------------------------------------------------------- toggles
    def _sync_dns_selector(self):
        if not hasattr(self, "dns_combo"):
            return
        if not self.settings.get("dnsFix", True):
            self.dns_var.set("Системный (сброс)")
            return
        name = self.settings.get("dnsName") or ""
        if name == "Свой" and self.settings.get("dnsPrimary"):
            self.dns_var.set("Свой")
        elif name in DNS_BY_NAME:
            self.dns_var.set(name)
        else:
            self.dns_var.set("Авто")

    def on_dns_selected(self, _event=None):
        if self.busy:
            self._sync_dns_selector()
            return
        self.apply_dns_choice(self.dns_var.get())

    def apply_dns_choice(self, name):
        if name == "Свой":
            self.show_custom_dns_dialog()
            return
        if name == "Системный (сброс)":
            self.settings["dnsFix"] = False
            save_settings(self.settings)
            self.toggles["dnsFix"].set_value(False)
            ok, detail = restore_youtube_dns()
            self.log_write(detail, "ok" if ok else "warn")
            self.settings = load_settings()
            self._sync_dns_selector()
            return

        if name == "Авто":
            ok, detail = set_youtube_dns()
            self.log_write(detail, "ok" if ok else "warn")
            self.settings = load_settings()
            self.toggles["dnsFix"].set_value(bool(self.settings.get("dnsFix", True)))
            self._sync_dns_selector()
            if not ok:
                messagebox.showerror("Zapret Control", "DNS не подобран: " + detail, parent=self.root)
            return

        if name not in DNS_BY_NAME:
            self._sync_dns_selector()
            return

        primary, secondary = DNS_BY_NAME[name]
        ad = get_best_adapter()
        if not ad or not ad["name"]:
            messagebox.showerror("Zapret Control", "Не найден активный сетевой адаптер", parent=self.root)
            self._sync_dns_selector()
            return

        apply_dns_pair(ad, primary, secondary)
        ok_y = system_resolves("www.youtube.com")
        ok_s = system_resolves("steamcommunity.com")
        self.settings["dnsFix"] = True
        self.settings["dnsName"] = name
        self.settings["dnsPrimary"] = primary
        self.settings["dnsSecondary"] = secondary
        save_settings(self.settings)
        self.toggles["dnsFix"].set_value(True)
        detail = f"DNS {name} · {primary} / {secondary}"
        if ok_y and ok_s:
            detail += " · ключевые домены резолвятся"
            self.log_write(detail, "ok")
        else:
            detail += " · ВНИМАНИЕ: youtube/steam не резолвятся"
            self.log_write(detail, "warn")
        self.set_statusbar(detail)
        self._sync_dns_selector()

    def apply_custom_dns(self, primary, secondary):
        ad = get_best_adapter()
        if not ad or not ad["name"]:
            return False, "не найден активный сетевой адаптер"
        apply_dns_pair(ad, primary, secondary)
        ok_y = system_resolves("www.youtube.com")
        ok_s = system_resolves("steamcommunity.com")
        self.settings["dnsFix"] = True
        self.settings["dnsName"] = "Свой"
        self.settings["dnsPrimary"] = primary
        self.settings["dnsSecondary"] = secondary
        save_settings(self.settings)
        self.toggles["dnsFix"].set_value(True)
        detail = f"DNS Свой · {primary}" + (f" / {secondary}" if secondary else "")
        if ok_y and ok_s:
            detail += " · ключевые домены резолвятся"
            self.log_write(detail, "ok")
        elif ok_y:
            detail += " · YouTube OK, Steam не резолвится"
            self.log_write(detail, "warn")
        else:
            detail += " · YouTube не резолвится"
            self.log_write(detail, "warn")
        self.set_statusbar(detail)
        self._sync_dns_selector()
        return ok_y, detail

    def show_custom_dns_dialog(self):
        if self.busy:
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("Свой DNS")
        dlg.configure(bg=BG)
        dlg.geometry("470x300")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        try:
            set_dark_titlebar(dlg)
        except Exception:
            pass

        tk.Label(dlg, text="СВОЙ DNS", bg=BG, fg=TEXT,
                 font=(FONT, 12, "bold"), anchor="w").place(x=20, y=16, width=300, height=26)
        tk.Label(dlg, text="Любые IPv4. Резервный можно оставить пустым.",
                 bg=BG, fg=MUTED, font=(FONT, 8), anchor="w").place(x=20, y=44, width=420, height=18)

        custom_saved = self.settings.get("dnsName") == "Свой"
        pv = tk.StringVar(value=(self.settings.get("dnsPrimary") if custom_saved else "") or "9.9.9.9")
        sv = tk.StringVar(value=(self.settings.get("dnsSecondary") if custom_saved else "") or "149.112.112.112")

        def add_entry(y, label, var):
            tk.Label(dlg, text=label, bg=BG, fg=MUTED, font=(FONT, 9),
                     anchor="w").place(x=20, y=y, width=110, height=24)
            ent = tk.Entry(dlg, textvariable=var, bg=CARD2, fg=TEXT,
                           insertbackground=TEXT, relief="flat",
                           highlightthickness=1, highlightbackground=BORDER,
                           highlightcolor=ACCENT, font=(FONT, 10))
            ent.place(x=140, y=y, width=300, height=30)

        add_entry(80, "Основной", pv)
        add_entry(130, "Резервный", sv)

        def apply():
            def valid(v):
                if not v:
                    return True
                try:
                    return ipaddress.ip_address(v).version == 4
                except Exception:
                    return False
            primary = pv.get().strip()
            secondary = sv.get().strip()
            if not primary:
                messagebox.showerror("Zapret Control", "Основной DNS не может быть пустым.", parent=dlg)
                return
            if not valid(primary) or not valid(secondary):
                messagebox.showerror("Zapret Control", "Введи корректный IPv4-адрес.", parent=dlg)
                return
            ok, detail = self.apply_custom_dns(primary, secondary)
            if ok:
                dlg.grab_release()
                dlg.destroy()
            else:
                messagebox.showwarning("Zapret Control", detail, parent=dlg)

        def cancel():
            dlg.grab_release()
            dlg.destroy()
            self._sync_dns_selector()

        RoundedButton(dlg, "ПРИМЕНИТЬ", 180, 36, ACCENT, BG, ACCENT_DARK,
                      apply, radius=8, font=(FONT, 9, "bold"),
                      parent_bg=BG).place(x=20, y=220)
        RoundedButton(dlg, "ОТМЕНА", 120, 36, CARD2, TEXT, HOVER,
                      cancel, radius=8, font=(FONT, 9),
                      parent_bg=BG).place(x=320, y=220)
        dlg.protocol("WM_DELETE_WINDOW", cancel)

    def on_dns_toggle(self, value):
        if value:
            name = self.dns_var.get() if hasattr(self, "dns_var") else "Авто"
            if name == "Свой":
                primary = self.settings.get("dnsPrimary")
                secondary = self.settings.get("dnsSecondary")
                if primary:
                    self.apply_custom_dns(primary, secondary)
                else:
                    self.show_custom_dns_dialog()
            elif name in DNS_BY_NAME:
                self.apply_dns_choice(name)
            else:
                self.apply_dns_choice("Авто")
        else:
            self.settings["dnsFix"] = False
            save_settings(self.settings)
            if hasattr(self, "dns_var"):
                self.dns_var.set("Системный (сброс)")
            ok, detail = restore_youtube_dns()
            self.log_write(detail, "ok" if ok else "warn")
            self.settings = load_settings()
            self._sync_dns_selector()

    def on_tunnels_toggle(self, value):
        self.settings["tunnels"] = bool(value)
        save_settings(self.settings)
        if value:
            for msg in disable_tunnels():
                self.log_write(msg)
        else:
            restore_tunnels()
            self.log_write("Teredo / ISATAP возвращены в состояние по умолчанию")

    def on_gameudp_toggle(self, value):
        self.settings["gameUdp"] = bool(value)
        save_settings(self.settings)
        self.log_write("Игровой UDP: " + ("включён" if value else "выключен"))
        if engine_running():
            self.restart_engine()

    def on_autostart_toggle(self, value):
        ok, detail = set_autostart(bool(value))
        if ok:
            self.settings["autoStart"] = bool(value)
            save_settings(self.settings)
            self.log_write("Автозапуск: " + ("включён" if value else "выключен"))
        else:
            self.toggles["autoStart"].set_value(not value)
            self.log_write(f"Автозапуск: ошибка — {detail}", "err")
            messagebox.showerror("Zapret Control", f"Не удалось изменить автозапуск:\n{detail}", parent=self.root)

    def on_stopexit_toggle(self, value):
        self.settings["stopOnExit"] = bool(value)
        save_settings(self.settings)

    # --------------------------------------------------------------- profiles
    def select_profile(self, key, no_restart=False):
        if self.busy or key not in PROFILES:
            return
        self.settings["profile"] = key
        save_settings(self.settings)
        for k, card in self.cards.items():
            card.set_selected(k == key)
        self.log_write(f"Профиль: {PROFILES[key][0]}")
        self.update_engine_visual()
        if engine_running() and not no_restart:
            self.log_write("Перезапуск движка с новым профилем…")
            self.restart_engine()

    # --------------------------------------------------------------- queue
    def _poll(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                kind = msg[0]
                if kind == "tune_progress":
                    self.log_write(f"  пробую стратегию: {msg[1]}")
                elif kind == "tune_done":
                    self._finish_tune(msg[1], msg[2], msg[3])
                elif kind == "tune_error":
                    self.set_busy(False)
                    self.log_write(f"Автоподбор: {msg[1]}", "err")
                    self.update_engine_visual()
                elif kind == "test_done":
                    self._finish_test(msg[1])
                elif kind == "test_error":
                    self.set_busy(False)
                    self.log_write(f"Проверка: {msg[1]}", "err")
                elif kind == "dns_progress":
                    self.log_write(f"  проверяю DNS: {msg[1]}")
                elif kind == "dns_done":
                    self._finish_dns(msg[1])
                elif kind == "dns_error":
                    self.set_busy(False)
                    self.log_write(f"DNS: {msg[1]}", "err")
                    self.update_engine_visual()
                elif kind == "diag_done":
                    self.set_busy(False)
                    self.update_engine_visual()
                    self.show_diagnostics(msg[1])
                elif kind == "diag_error":
                    self.set_busy(False)
                    self.log_write(f"Диагностика: {msg[1]}", "err")
        except queue.Empty:
            pass

        self._tail_engine_logs()

        now = time.time()
        if now - self.last_visual > 2.0 and not self.busy:
            self.last_visual = now
            self.update_engine_visual()

        self.root.after(250, self._poll)

    def _tail_engine_logs(self):
        for key, path in (("out", ENGINE_OUT), ("err", ENGINE_ERR)):
            try:
                if not path.exists():
                    continue
                size = path.stat().st_size
                if size < self.log_pos[key]:
                    self.log_pos[key] = 0
                if size <= self.log_pos[key]:
                    continue
                with path.open("rb") as f:
                    f.seek(self.log_pos[key])
                    data = f.read()
                    self.log_pos[key] = f.tell()
                text = data.decode("utf-8", errors="replace")
                text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
                skip = (
                    "loading ", "loaded ", "we have ", "github version",
                    "windivert initialized", "windivert ", "capture is started",
                )
                for line in text.splitlines():
                    line = line.strip()
                    if line and not line.lower().startswith(skip):
                        self.log_write("[winws] " + line)
            except Exception:
                pass

    # --------------------------------------------------------------- close
    def on_close(self):
        save_settings(self.settings)
        running = engine_running()
        stop = bool(self.settings.get("stopOnExit", False))
        if running and not stop and not self.preview:
            answer = messagebox.askyesnocancel(
                "Zapret Control",
                "Движок работает.\n\nОстановить его перед выходом?\n"
                "«Нет» — оставить работать в фоне.",
                parent=self.root,
            )
            if answer is None:
                return
            stop = bool(answer)
        if stop:
            stop_engine()
        self.root.destroy()


# --------------------------------------------------------------------------
# window effects
# --------------------------------------------------------------------------
def set_dark_titlebar(root):
    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        if not hwnd:
            hwnd = root.winfo_id()
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), 4)
        value = ctypes.c_int(2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(value), 4)
        for attr, color in ((35, 0x00120D0B), (36, 0x00F0EBE8), (34, 0x00392C27)):
            c = ctypes.c_uint(color)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(c), 4)
    except Exception:
        pass


def set_dpi_aware():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def single_instance():
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\ZapretControlPy")
    if ctypes.windll.kernel32.GetLastError() == 183:
        ctypes.windll.user32.MessageBoxW(
            0, "Zapret Control уже запущен.", "Zapret Control", 0x40
        )
        return None
    return handle


def elevate():
    if getattr(sys, "frozen", False):
        exe = str(Path(sys.executable).resolve())
        params = "-autostart" if AUTOSTART else ""
    else:
        script = Path(__file__).resolve()
        exe = str(pythonw_path())
        params = subprocess.list2cmdline([str(script)] + (["-autostart"] if AUTOSTART else []))
    rc = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", exe, params, str(APP_ROOT), 1
    )
    if rc <= 32:
        ctypes.windll.user32.MessageBoxW(
            0,
            "Не удалось запросить права администратора.\n"
            "Запустите файл от имени администратора.",
            "Zapret Control",
            0x10,
        )


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def main():
    global AUTOSTART
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-autostart", "--autostart", action="store_true")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--offscreen", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--no-elevate", action="store_true")
    args, _unknown = parser.parse_known_args()
    AUTOSTART = bool(args.autostart)

    if args.selftest:
        lines = [f"version = {APP_VERSION}", "core ok", f"root = {APP_ROOT}", f"build args: {len(build_args('universal', 'hostfakesplit', False))}"]
        for row in diagnostics():
            lines.append(f"{row['status']:4} {row['name']}: {row['detail']}")
        text = "\n".join(lines)
        try:
            (APP_ROOT / "selftest.txt").write_text(text, encoding="utf-8")
        except Exception:
            pass
        print(text)
        return

    # run without a console window (source version only)
    if (not args.preview and not getattr(sys, "frozen", False)
            and not sys.executable.lower().endswith("pythonw.exe")):
        pw = pythonw_path()
        if pw.name.lower() == "pythonw.exe" and pw.exists():
            subprocess.Popen(
                [str(pw), str(Path(__file__).resolve())] + (["-autostart"] if AUTOSTART else []),
                creationflags=DETACHED_PROCESS,
            )
            return

    set_dpi_aware()

    if not is_admin() and not args.preview and not args.no_elevate:
        elevate()
        return

    mutex = None if args.preview else single_instance()
    if not args.preview and mutex is None:
        return

    root = tk.Tk()
    app = App(root, preview=args.preview, offscreen=args.offscreen)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

