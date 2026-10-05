#!/usr/bin/env python3
"""Install only cabinet ROM-project docs and the server client; no restarts.

Run as root from this checkout. Existing differing files are backed up, never
deleted. No ROMs, saves, daemon units, or other service configurations change.
"""
import argparse
import hashlib
import os
from pathlib import Path
import pwd
import shutil
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--roms", type=Path, default=Path("/roms"))
    parser.add_argument("--user", default=os.environ.get("ARCADE_USER", "arcade"))
    parser.add_argument("--server", help="seed the cabinet URL only if cabinet.env does not exist")
    args = parser.parse_args()
    account = pwd.getpwnam(args.user)
    stamp = time.strftime("%Y%m%d-%H%M%S") + "-" + str(os.getpid())

    def install(source, dest, mode, owner):
        data = source.read_bytes()
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.read_bytes() == data:
            print(f"already current: {dest}")
            return
        if dest.exists() or dest.is_symlink():
            backup = dest.with_name(dest.name + ".before-rom-client-" + stamp)
            shutil.copy2(dest, backup)
            print(f"preserved previous file: {backup}")
        with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as f:
            f.write(data)
            tmp = Path(f.name)
        tmp.chmod(mode)
        os.chown(tmp, *owner)
        os.replace(tmp, dest)
        print(f"installed: {dest} sha256={hashlib.sha256(data).hexdigest()}")

    install(args.source_root / "server/arcadectl", Path("/usr/local/bin/arcadectl"), 0o755, (0, 0))
    templates = args.source_root / "cabinet/roms-project"
    for source in sorted(templates.rglob("*.md")):
        install(source, args.roms / source.relative_to(templates), 0o644, (account.pw_uid, account.pw_gid))

    def link(dest, target):
        dest.parent.mkdir(parents=True, exist_ok=True)
        relative = os.path.relpath(target, dest.parent)
        if dest.is_symlink() and os.readlink(dest) == relative:
            return
        if dest.exists() or dest.is_symlink():
            print(f"preserving existing skill entry: {dest}")
            return
        dest.symlink_to(relative)
        os.lchown(dest, account.pw_uid, account.pw_gid)
        print(f"linked: {dest} -> {relative}")

    skill = args.roms / "skills/arcade-roms"
    link(args.roms / "SKILL.md", skill / "SKILL.md")
    for provider in (".agents", ".claude"):
        link(args.roms / provider / "skills/arcade-roms", skill)
    # Let cabinet-side agents maintain their project's documentation.
    for directory in (args.roms / "skills", skill, args.roms / ".agents", args.roms / ".agents/skills",
                      args.roms / ".claude", args.roms / ".claude/skills"):
        os.chown(directory, account.pw_uid, account.pw_gid)

    config = Path("/etc/arcade/cabinet.env")
    if args.server and not config.exists():
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("# Cabinet/server client URL\nCABD_MEDIA_BASE=" + args.server.rstrip("/") + "\n")
        config.chmod(0o644)
        print(f"seeded: {config}")
    print("Ready: run arcadectl --json status directly from the cabinet ROMs project.")


if __name__ == "__main__":
    main()
