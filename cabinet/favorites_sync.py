"""Bridge Pegasus's saved stars to the server-authoritative Favorites list.

Keep an acknowledged snapshot so removals survive daemon restarts and failed
requests retry. Only native star changes are sent; server-only favorites are
never removed merely because Pegasus has not starred them.
"""
import asyncio
import json
import os
from pathlib import Path

import aiohttp


def read_favorites(path, roms_dir):
    root = Path(roms_dir)
    favorites = set()
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            relative = Path(line).relative_to(root)
        except ValueError:
            continue
        if len(relative.parts) == 2 and ".." not in relative.parts:
            favorites.add(relative.as_posix())
    return favorites


class FavoritesSync:
    def __init__(self, native_path, checkpoint, roms_dir):
        self.native_path = Path(native_path)
        self.checkpoint = Path(checkpoint)
        self.roms_dir = roms_dir
        try:
            self.acknowledged = set(json.loads(self.checkpoint.read_text()))
        except FileNotFoundError:
            self.acknowledged = set()

    async def sync(self, send):
        try:
            current = read_favorites(self.native_path, self.roms_dir)
        except FileNotFoundError:
            return  # Pegasus not installed/started, or replacing its file
        if current == self.acknowledged:
            return
        for favorite in sorted(current - self.acknowledged):
            await send("add", favorite)
        for favorite in sorted(self.acknowledged - current):
            await send("remove", favorite)
        # Advance only after all requests succeed; add/remove are idempotent.
        self.checkpoint.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.checkpoint.with_suffix(".tmp")
        temporary.write_text(json.dumps(sorted(current)))
        os.replace(temporary, self.checkpoint)
        self.acknowledged = current


async def run(server_url, roms_dir, cache_path, log):
    native = os.environ.get("PEGASUS_FAVORITES_FILE", str(
        Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        / "pegasus-frontend/favorites.txt"))
    checkpoint = str(Path(cache_path).with_name("pegasus-favorites-sync.json"))
    bridge = None
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        async def send(action, favorite):
            async with session.post(server_url.rstrip("/") + "/favorites/" + action,
                                    json={"id": favorite}) as response:
                response.raise_for_status()
                await response.json()
            log(f"Pegasus favorite {action}: {favorite}")

        while True:
            try:
                if bridge is None:
                    bridge = FavoritesSync(native, checkpoint, roms_dir)
                await bridge.sync(send)
            except Exception as error:
                log(f"Pegasus favorites sync deferred: {error!r}")
            await asyncio.sleep(2)
