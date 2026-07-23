# JOURNAL

Append-only. One entry per iteration, with evidence (commands + exit codes).

---

## Iteration 1 — 2026-07-23 — Repo bootstrap

**Unit:** First-run bootstrap: copy spec into repo, create ROADMAP.md, state/STATE.md,
state/JOURNAL.md, ASSUMPTIONS.md, HARDWARE-TODO.md, NIGHTLY-REPORT.md, .gitignore.

**Soak check:** N/A — soak stack not built yet (M-SIM pending).

**Evidence:**
- `docker --version` → `Docker version 29.3.1, build c2be9cc` (exit 0)
- `docker compose version` → `v5.1.1` (exit 0)
- `uname -a` → Linux x86_64 (exit 0) — dev box is Linux, not Windows; recorded A-1.
- `cp <upload> arcade-cabinet-spec.md` → exit 0
- Repo had no commits (`git log` exit 128, "does not have any commits yet") — confirmed
  true first run on branch `claude/arcade-cabinet-impl-nn0cka`.
- Commit + push evidence appended below by the commit step.

**Verification:** Docs-only unit — no tests to run; subagent verification starts with the
first code unit (sim rig skeleton, iteration 2).

**Outcome:** GREEN (bootstrap complete).
