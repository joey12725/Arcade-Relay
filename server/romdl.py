#!/usr/bin/env python3
"""ROMDL adapter (ROM-1, spec §6.4).

The real ROMDL package's API is unconfirmed (OQ-4); per A-5 the assumed
contract is search(query) -> candidates and download(id) -> files. This
module implements that contract with a FAKE backend (deterministic
generated ROM + art bytes) behind the same functions the real package
will slot into — swap the two _impl functions when ROMDL lands.
"""
import hashlib
import os

STAGING = os.environ.get("ROMDL_STAGING", "/tmp/romdl-staging")

FAKE_CATALOG = [
    {"id": "ms3", "title": "Metal Slug 3", "system": "arcade"},
    {"id": "ms1", "title": "Metal Slug", "system": "arcade"},
    {"id": "smw", "title": "Super Mario World", "system": "snes"},
    {"id": "sor2", "title": "Streets of Rage 2", "system": "genesis"},
    {"id": "mmz", "title": "Mega Man Zero", "system": "gba"},
    {"id": "sotn", "title": "Symphony of the Night", "system": "psx"},
]


def search(query):
    q = query.lower()
    return [c for c in FAKE_CATALOG if q in c["title"].lower()]


def download(rom_id):
    """Returns {rom_path, art_path, sha256, system, filename}. Deterministic
    fake payload seeded by id, so checksums are stable across runs."""
    entry = next((c for c in FAKE_CATALOG if c["id"] == rom_id), None)
    if entry is None:
        raise KeyError(f"unknown rom id {rom_id}")
    os.makedirs(STAGING, exist_ok=True)
    fname = entry["title"].replace(" ", "_") + ".bin"
    rom_path = os.path.join(STAGING, fname)
    blob = hashlib.sha256(rom_id.encode()).digest() * 8192   # 256KB
    with open(rom_path, "wb") as f:
        f.write(blob)
    art_path = os.path.join(STAGING, fname + ".png")
    # tiny valid-enough art placeholder (the theme only needs a file)
    with open(art_path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + hashlib.sha256(
            (rom_id + "art").encode()).digest())
    return {"rom_path": rom_path, "art_path": art_path,
            "sha256": hashlib.sha256(blob).hexdigest(),
            "system": entry["system"], "filename": fname,
            "title": entry["title"]}
