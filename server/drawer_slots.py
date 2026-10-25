"""Connection-to-drawer mapping for cloner and build machines (#1485)."""

from __future__ import annotations

import json
import re

PORT_MAX = 32
DRAWER_MAX = 8
_SATA = re.compile(r"sata:([1-9][0-9]?)\Z")
_NVME = re.compile(r"nvme:[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]\Z")
#: ‏#2001: "NVMe n" שנוסף ידנית לפני שנראה בו כונן — בלי כתובת PCI (נדב 22/10).
#: ה-hello הראשון שמדווח NVMe n-י (לפי סדר כתובות ה-PCI) משלים אותו ל-nvme:<PCI>.
_NVME_ORD = re.compile(r"nvme#([1-8])\Z")
_OPTICAL_DEV = re.compile(r"sr[0-9]{1,2}\Z")


def _key(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    sata = _SATA.fullmatch(raw)
    if sata and int(sata.group(1)) <= PORT_MAX:
        return raw
    if _NVME.fullmatch(raw) or _NVME_ORD.fullmatch(raw):
        return raw
    return None


def order(key: str) -> tuple:
    """סדר החיבורים (#2001: "אין גרירה — הסדר הוא סדר החיבורים"): SATA לפי
    הפורט, אחריהם NVMe לפי כתובת ה-PCI, ואחריהם NVMe שעוד לא נראה."""
    if key.startswith("sata:"):
        return (0, int(key[5:]), "")
    if key.startswith("nvme:"):
        return (1, 0, key)
    return (2, int(key[5:]), "")


def resolve_ordinals(slots: list[str], disks) -> list[str]:
    """‏``nvme#n`` → ‏``nvme:<PCI>`` של ה-NVMe ה-n-י שהמכונה מדווחת (לפי סדר
    הכתובות), כשהוא קיים ואינו כבר במיפוי. השאר — כמו שהם."""
    pcis = sorted({f"nvme:{d['pci']}" for d in disks or []
                   if isinstance(d, dict) and isinstance(d.get("pci"), str)
                   and _NVME.fullmatch(f"nvme:{d['pci']}")})
    out = list(slots)
    for i, key in enumerate(out):
        m = _NVME_ORD.fullmatch(key)
        if m and int(m.group(1)) <= len(pcis) and pcis[int(m.group(1)) - 1] not in out:
            out[i] = pcis[int(m.group(1)) - 1]
    return out


def parse(raw) -> list[str]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= DRAWER_MAX:
        raise ValueError("מיפוי המגירות חייב להכיל בין 1 ל־8 חיבורים")
    result: list[str] = []
    for item in raw:
        key = _key(item)
        if key is None:
            raise ValueError("חיבור לא תקין — sata:1..32, nvme:<כתובת PCI> או nvme#1..8")
        if key in result:
            raise ValueError(f"חיבור כפול: {key}")
        result.append(key)
    return result


def default(count: int) -> list[str]:
    return [f"sata:{port}" for port in range(1, count + 1)]


def effective(drawer_count: int, stored_json: str | None,
              model_slots: list[str] | None = None) -> list[str]:
    """‏#2001: מכונה (‏``stored_json``) ← דגם (‏``model_slots``) ← "מגירה k = ‏sata:k"."""
    if stored_json is not None:
        return parse(json.loads(stored_json))
    return list(model_slots) if model_slots else default(drawer_count)


def key_of(disk: dict) -> str | None:
    port = disk.get("port")
    if isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= PORT_MAX:
        return f"sata:{port}"
    return _key(f"nvme:{disk.get('pci')}") if isinstance(disk.get("pci"), str) else None


def drawer_of(slots: list[str], disk: dict) -> int | None:
    key = key_of(disk)
    try:
        return slots.index(key) + 1 if key is not None else None
    except ValueError:
        return None


def sata_ports(slots: list[str]) -> set[int]:
    return {int(key[5:]) for key in slots if _SATA.fullmatch(key)}


def clean_optical(raw) -> list | None:
    if not isinstance(raw, list) or len(raw) > DRAWER_MAX:
        return None
    cleaned = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        dev, model, port = item.get("dev"), item.get("model"), item.get("port")
        if not isinstance(dev, str) or _OPTICAL_DEV.fullmatch(dev) is None:
            return None
        if not isinstance(model, str):
            return None
        if port is not None and (isinstance(port, bool) or not isinstance(port, int)
                                 or not 1 <= port <= PORT_MAX):
            return None
        cleaned.append({"dev": dev, "model": model[:80], "port": port})
    return cleaned
