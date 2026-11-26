"""Discover video-capable AirPlay and Google Cast receivers on the local network."""

import asyncio
import uuid
from dataclasses import dataclass

from zeroconf import IPVersion, ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from .network import Lan, interfaces

AIRPLAY = "_airplay._tcp.local."
CAST = "_googlecast._tcp.local."
AIRPLAY_SCREEN = 1 << 7
CAST_VIDEO_OUT = 1


@dataclass(frozen=True)
class Device:
    name: str
    protocol: str
    address: str
    port: int
    identity: str
    model: str
    lan: Lan

    @property
    def key(self):
        return self.protocol, self.identity or self.address


def text(value):
    if not value:
        return ""
    return "".join(char for char in value.decode("utf-8", errors="replace") if char.isprintable())[:100]


def receiver(info, service_type, lans):
    properties = info.properties
    try:
        if service_type == AIRPLAY:
            model = text(properties.get(b"model"))
            features = int(text(properties.get(b"features")).split(",")[0] or "0", 0)
            if not (features & AIRPLAY_SCREEN or model.startswith("AppleTV")):
                return None
            name = info.name.removesuffix("." + AIRPLAY)
            identity = text(properties.get(b"deviceid")).upper()
            protocol = "AirPlay"
        else:
            if not int(text(properties.get(b"ca")) or "0") & CAST_VIDEO_OUT:
                return None
            model = text(properties.get(b"md"))
            name = text(properties.get(b"fn"))
            identity = str(uuid.UUID(text(properties.get(b"id"))))
            protocol = "Google Cast"
    except ValueError:
        return None

    name = "".join(char for char in name if char.isprintable())[:100]
    for address in info.parsed_addresses(IPVersion.V4Only):
        for lan in lans:
            if address != lan.host and lan.permits(address) and 1 <= info.port <= 65535:
                return Device(name or model or address, protocol, address, info.port, identity, model, lan)
    return None


async def discover(seconds=3.5, lans=None):
    lans = interfaces() if lans is None else lans
    if not lans:
        return []
    zc = AsyncZeroconf(interfaces=[lan.host for lan in lans], ip_version=IPVersion.V4Only)
    names = set()

    def update(zeroconf, service_type, name, state_change):
        entry = (service_type, name)
        if state_change == ServiceStateChange.Removed:
            names.discard(entry)
        else:
            names.add(entry)

    browser = AsyncServiceBrowser(zc.zeroconf, [AIRPLAY, CAST], handlers=[update])
    try:
        await asyncio.sleep(seconds)

        async def resolve(service_type, name):
            info = AsyncServiceInfo(service_type, name)
            if await info.async_request(zc.zeroconf, 1000):
                return receiver(info, service_type, lans)
            return None

        found = await asyncio.gather(*(resolve(*entry) for entry in sorted(names)))
        unique = {device.key: device for device in found if device}
        return sorted(unique.values(), key=lambda device: (device.name.casefold(), device.protocol))
    finally:
        await browser.async_cancel()
        await zc.async_close()
