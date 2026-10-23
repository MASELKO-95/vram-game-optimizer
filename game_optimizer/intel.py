"""Read Intel memory regions through the read-only i915 and Xe DRM queries.

Layouts follow include/uapi/drm/{i915,xe}_drm.h. No GPU contexts, buffers,
privilege changes or external monitoring daemons are created.
"""
from __future__ import annotations

import ctypes
import fcntl
import os
from pathlib import Path
import re
import struct


# DRM_IOWR('d', DRM_COMMAND_BASE + command, query structure).
I915_QUERY = 0xC0106479  # command 0x39, sizeof(drm_i915_query) == 16
XE_DEVICE_QUERY = 0xC0286440  # command 0, sizeof(drm_xe_device_query) == 40
MAX_QUERY_BYTES = 1048576
REGION_BYTES = 88


def query_memory_regions(fd: int, driver: str) -> bytes:
    """Two-stage size/data query, with bounded allocation and zeroed reserves."""
    if driver == "i915":
        item = ctypes.create_string_buffer(24)
        struct.pack_into("=QiIQ", item, 0, 4, 0, 0, 0)  # MEMORY_REGIONS
        query = bytearray(struct.pack("=IIQ", 1, 0, ctypes.addressof(item)))
        fcntl.ioctl(fd, I915_QUERY, query, True)
        length = struct.unpack_from("=i", item, 8)[0]
    elif driver == "xe":
        query = bytearray(struct.pack("=QIIQQQ", 0, 1, 0, 0, 0, 0))
        fcntl.ioctl(fd, XE_DEVICE_QUERY, query, True)
        length = struct.unpack_from("=I", query, 12)[0]
    else:
        raise ValueError("Unsupported Intel DRM driver.")
    if not 8 <= length <= MAX_QUERY_BYTES:
        raise ValueError("Invalid Intel memory query size.")
    data = ctypes.create_string_buffer(length)
    if driver == "i915":
        struct.pack_into("=Q", item, 16, ctypes.addressof(data))
        fcntl.ioctl(fd, I915_QUERY, query, True)
        returned = struct.unpack_from("=i", item, 8)[0]
    else:
        struct.pack_into("=Q", query, 16, ctypes.addressof(data))
        fcntl.ioctl(fd, XE_DEVICE_QUERY, query, True)
        returned = struct.unpack_from("=I", query, 12)[0]
    if returned != length:
        raise ValueError("Intel memory query failed or changed size.")
    return data.raw


def parse_memory_regions(raw: bytes, driver: str) -> dict:
    """Keep local memory separate from system-memory regions and BAR sizes."""
    if driver not in ("i915", "xe"):
        raise ValueError("Unsupported Intel DRM driver.")
    header = 16 if driver == "i915" else 8
    if len(raw) < header:
        raise ValueError("Truncated Intel memory query.")
    count = struct.unpack_from("=I", raw)[0]
    if not 1 <= count <= 256 or len(raw) != header + count * REGION_BYTES:
        raise ValueError("Invalid Intel memory region count or size.")
    local, shared, seen = [], [], set()
    for index in range(count):
        memory_class, instance, _, total, counter = struct.unpack_from("=HHIQQ", raw, header + index * REGION_BYTES)
        key = memory_class, instance
        if key in seen or memory_class not in (0, 1) or not 0 < total < 2 ** 63:
            raise ValueError("Invalid or duplicate Intel memory region.")
        seen.add(key)
        if memory_class == 0:
            shared.append(total)
        else:
            used = total - counter if driver == "i915" else counter
            local.append((total, used if 0 <= used <= total else None))
    if not local:
        return {"memory_kind": "shared", "total_mib": 0, "used_mib": None, "free_mib": None,
                "shared_total_mib": sum(shared) / 1048576,
                "memory_note": "GPU współdzieli RAM z systemem; brak osobnej puli VRAM."}
    total = sum(region[0] for region in local)
    used = sum(region[1] for region in local) if all(region[1] is not None for region in local) else None
    # Older kernels return zero usage/full free memory when accounting is
    # restricted. Treat this ambiguous value as unknown, never as freed VRAM.
    if used == 0:
        used = None
    return {"memory_kind": "dedicated", "total_mib": total / 1048576,
            "used_mib": used / 1048576 if used is not None else None,
            "free_mib": (total - used) / 1048576 if used is not None else None,
            "memory_note": "Brak wiarygodnego odczytu zajętości VRAM (sterownik lub uprawnienia)." if used is None else ""}


def read_intel_gpus(drm_root: Path = Path("/sys/class/drm"), dev_root: Path = Path("/dev/dri")) -> list[dict]:
    gpus, seen = [], set()
    for card in sorted(drm_root.glob("card*")):
        if not re.fullmatch(r"card\d+", card.name):
            continue
        device = card / "device"
        try:
            if int((device / "vendor").read_text().strip(), 16) != 0x8086:
                continue
            physical = device.resolve(strict=True)
            driver = (device / "driver").resolve(strict=True).name
            if driver not in ("i915", "xe") or physical in seen:
                continue
        except (OSError, ValueError):
            continue
        memory = {"memory_kind": "unknown", "total_mib": None, "used_mib": None, "free_mib": None,
                  "memory_note": "Brak odczytu pamięci Intel DRM. Sprawdź dostęp do /dev/dri i obsługę zapytań w sterowniku."}
        nodes = sorted((device / "drm").glob("renderD*")) + [card]
        for node in nodes:
            if not re.fullmatch(r"(?:renderD|card)\d+", node.name):
                continue
            try:
                fd = os.open(dev_root / node.name, os.O_RDONLY | os.O_CLOEXEC)
                try:
                    memory = parse_memory_regions(query_memory_regions(fd, driver), driver)
                finally:
                    os.close(fd)
                break
            except (OSError, ValueError):
                continue
        try:
            product = (device / "product_name").read_text().strip()
        except OSError:
            product = ""
        seen.add(physical)
        gpus.append({"id": physical.name, "vendor": "Intel", "backend": driver + "-drm-query",
                     "card": card.name, "name": product or f"Intel GPU ({physical.name}, {driver})",
                     "processes": [], **memory})
    return gpus
