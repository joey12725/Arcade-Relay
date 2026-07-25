#!/usr/bin/env python3
"""Drive the web UI in a REAL browser (§6.3 + CTL-3).

Greping the HTML for function names proves nothing about whether the page
works. This loads /ui in Chromium and interacts with it the way a person
would: check the library reflects ROMs added while the page stays open (no
reload), and run the interactive remap flow end to end (capture a physical
button relayed from cabd, click a logical target, verify the mapping is
committed to the state repo).

Runs on the HOST against the sim's published server port; the cabinet FIFO
and the state repo are poked through docker compose exec.
"""
import json
import subprocess
import sys
import time
import urllib.request

SIM = ["docker", "compose", "-f", "sim/docker-compose.yml"]
CHROMIUM = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"


def sim_exec(service, *cmd, check=True):
    p = subprocess.run([*SIM, "exec", "-T", service, *cmd],
                       capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"{service} {cmd}: {p.stderr.strip()[:300]}")
    return p.stdout


def srv_json(path):
    return json.loads(sim_exec("server", "python3", "-c",
        f"import urllib.request;print(urllib.request.urlopen("
        f"'http://localhost:8000{path}').read().decode())"))


def main(base_url):
    from playwright.sync_api import sync_playwright

    # start from an empty library so "appears without reload" is observable
    sim_exec("cabinet", "sh", "-c", "rm -f /roms/snes/*.sfc /roms/gb/*.gb")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=CHROMIUM)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base_url + "/ui", wait_until="networkidle")

        # --- S5: library must pick up ROMs delivered while the page is open
        page.wait_for_selector("#library li")
        before = page.inner_text("#library")
        assert "no games" in before, f"expected empty library, got: {before!r}"
        sim_exec("cabinet", "sh", "-c",
                 "touch /roms/snes/smw.sfc /roms/snes/kart.sfc /roms/gb/loop.gb")
        page.wait_for_function(
            "() => document.querySelectorAll('#library li').length >= 3",
            timeout=25000)          # refresh cycle is 5s
        lib = page.inner_text("#library")
        for want in ("snes/smw.sfc", "snes/kart.sfc", "gb/loop.gb"):
            assert want in lib, f"{want} missing from live library: {lib!r}"
        page.wait_for_function(
            "() => document.querySelectorAll('#libSystem option').length >= 3",
            timeout=25000)
        print("S5 ok: library and system filter updated with no reload")

        # --- filter actually filters
        page.select_option("#libSystem", "gb")
        page.wait_for_function(
            "() => !document.getElementById('library').innerText.includes('snes/')",
            timeout=15000)
        assert "gb/loop.gb" in page.inner_text("#library")
        page.select_option("#libSystem", "")
        page.wait_for_function(
            "() => document.getElementById('library').innerText.includes('snes/')",
            timeout=15000)
        print("filter ok: system dropdown narrows the list")

        # --- favorite toggle from the UI reaches state
        page.click("#library li:has-text('gb/loop.gb') button:has-text('☆')")
        deadline = time.time() + 15
        while time.time() < deadline:
            if "gb/loop.gb" in srv_json("/favorites")["favorites"]:
                break
            time.sleep(0.5)
        else:
            raise AssertionError("favorite click never reached state")
        print("favorite toggle ok: click landed in state/library/favorites.yaml")

        # --- CTL-3: capture a physical button, click a logical target
        page.select_option("#scope", "system:snes")
        page.click("#captureBtn")
        time.sleep(0.6)                       # capture window is open (3s)
        sim_exec("cabinet", "sh", "-c", 'echo "select down" > /tmp/input-events')
        page.wait_for_function(
            "() => document.getElementById('captured').textContent === 'select'",
            timeout=15000)
        page.click("#targets button:has-text('r')")
        page.wait_for_function(
            "() => document.getElementById('mapStatus').textContent"
            ".includes('committed')", timeout=15000)
        deadline = time.time() + 15
        while time.time() < deadline:
            snes = sim_exec("server", "cat",
                            "/statework/controllers/systems/snes.yaml")
            if "select: r" in snes:
                break
            time.sleep(0.5)
        else:
            raise AssertionError(f"remap never committed; snes.yaml: {snes!r}")
        log = sim_exec("server", "git", "-C", "/statework", "log", "--oneline", "-3")
        assert "select=r" in log, f"no commit for the remap: {log!r}"
        print("CTL-3 ok: press-button -> click-target -> committed to state")

        assert not errors, f"page errors: {errors}"
        browser.close()
    print("WEBUI-LIVE PASS")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000")
