# Install games from the cabinet's ROMs project

This folder (`/roms`) is the cabinet's installed game library, and it doubles
as a coding-agent project. Open it in your agent of choice on the cabinet and
ask in plain language, for example:

> Install "Star Patrol" for the NES from the server library. Wait until it
> is ready and verify the installed file. Don't launch it yet.

The agent does the whole task from this project. The CLI is installed and the
server address is already configured. The server stages and verifies the file
from its library, then it arrives here automatically and shows up in Pegasus.

**Agent instructions:** [AGENTS.md](AGENTS.md)

**Reusable skill:** [skills/arcade-roms/SKILL.md](skills/arcade-roms/SKILL.md)
(also linked as `SKILL.md` at the project root).

Installed games live in the system folders below this directory. Documentation
files are not games and are not included in Pegasus collections.
