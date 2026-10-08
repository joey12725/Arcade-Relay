import pathlib
import sys
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "cabinet"))
from favorites_sync import FavoritesSync, read_favorites


def test_native_paths_across_systems(tmp_path):
    native = tmp_path / "favorites.txt"
    native.write_text("# Stars\n/roms/nes/Mario (USA).nes\n"
                      "/roms/snes/World.sfc\n/roms/psx/Pepsiman.cue\n"
                      "/other/game.nes\n/roms/nes/../bad.nes\n")
    assert read_favorites(native, "/roms") == {
        "nes/Mario (USA).nes", "snes/World.sfc", "psx/Pepsiman.cue"}


def test_import_then_changes_retry_and_survive_restart(tmp_path):
    import asyncio

    async def check():
        native = tmp_path / "favorites.txt"
        checkpoint = tmp_path / "checkpoint.json"
        native.write_text("/roms/nes/Mario.nes\n/roms/snes/World.sfc\n")
        bridge = FavoritesSync(native, checkpoint, "/roms")
        send = AsyncMock()
        await bridge.sync(send)
        assert send.call_args_list == [
            (("add", "nes/Mario.nes"),), (("add", "snes/World.sfc"),)]
        send.reset_mock()
        await FavoritesSync(native, checkpoint, "/roms").sync(send)
        send.assert_not_awaited()

        native.write_text("/roms/snes/World.sfc\n/roms/psx/Pepsiman.cue\n")
        send.side_effect = RuntimeError("offline")
        with pytest.raises(RuntimeError):
            await bridge.sync(send)
        assert bridge.acknowledged == {"nes/Mario.nes", "snes/World.sfc"}
        bridge = FavoritesSync(native, checkpoint, "/roms")
        send.reset_mock()
        send.side_effect = None
        await bridge.sync(send)
        assert send.call_args_list == [
            (("add", "psx/Pepsiman.cue"),), (("remove", "nes/Mario.nes"),)]
        assert bridge.acknowledged == {"psx/Pepsiman.cue", "snes/World.sfc"}

        native.unlink()
        send.reset_mock()
        await bridge.sync(send)
        send.assert_not_awaited()
        assert bridge.acknowledged == {"psx/Pepsiman.cue", "snes/World.sfc"}

    asyncio.run(check())
