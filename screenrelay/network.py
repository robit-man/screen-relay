"""Find LAN interfaces and the current desktop playback monitor."""

import ipaddress
import json
from dataclasses import dataclass
from subprocess import DEVNULL, SubprocessError, check_output


@dataclass(frozen=True)
class Lan:
    name: str
    address: ipaddress.IPv4Interface

    @property
    def host(self):
        return str(self.address.ip)

    def permits(self, host):
        try:
            return ipaddress.ip_address(host) in self.address.network
        except ValueError:
            return False


def output(*args):
    return check_output(args, text=True, stderr=DEVNULL, timeout=2)


def interfaces():
    try:
        links = json.loads(output("ip", "-j", "-4", "addr", "show", "up"))
        routes = json.loads(output("ip", "-j", "-4", "route", "show", "default"))
    except (OSError, SubprocessError, ValueError):
        return []
    preferred = {route.get("dev") for route in routes}
    available = []
    for link in links:
        name = link["ifname"]
        if "BROADCAST" not in link.get("flags", ()) or "LOWER_UP" not in link.get("flags", ()) or name.startswith(("docker", "br-", "veth")):
            continue
        for info in link["addr_info"]:
            if info.get("scope") == "global":
                address = ipaddress.IPv4Interface(f'{info["local"]}/{info["prefixlen"]}')
                available.append(Lan(name, address))
    return sorted(available, key=lambda lan: (lan.name not in preferred, lan.name, lan.host))


def playback_monitor():
    try:
        sink = output("pactl", "get-default-sink").strip()
        names = [line.split()[1] for line in output("pactl", "list", "short", "sources").splitlines()]
    except (OSError, SubprocessError, IndexError) as exc:
        raise RuntimeError("Desktop audio is unavailable. Turn off desktop audio to share video only.") from exc
    if not sink or f"{sink}.monitor" not in names:
        raise RuntimeError("The default audio output has no monitor. Choose an audio output or turn off desktop audio.")
    return f"{sink}.monitor"
