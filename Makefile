.PHONY: test lint dev-server demo-cabinet media sim-up sim-down sim-test test-m0 accept-m0 test-fakes test-m1 soak-up soak-down soak-status

# --- no-Docker dev loop -------------------------------------------------
DEV_STATE ?= .runtime/state
DEV_PORT ?= 8000

test:
	python3 -m pytest -q

lint:
	python3 -m ruff check .

# arcaded against a throwaway copy of state/ (seeded once as a git repo).
dev-server:
	@if [ ! -d $(DEV_STATE)/.git ]; then \
		mkdir -p $(DEV_STATE) && cp -r state/. $(DEV_STATE)/ && \
		git -C $(DEV_STATE) init -q && git -C $(DEV_STATE) add -A && \
		git -C $(DEV_STATE) -c user.name=arcaded -c user.email=arcaded@localhost commit -qm seed; \
	fi
	STATE_DIR=$(DEV_STATE) ROMDL_STAGING=.runtime/staging PORT=$(DEV_PORT) python3 server/arcaded.py

# A pretend cabinet that dials dev-server, so the dashboard has a game running.
demo-cabinet:
	python3 sim/demo_cabinet.py ws://127.0.0.1:$(DEV_PORT)/cab

sim-up:
	cd sim && docker compose up -d --build --wait --wait-timeout 300

sim-down:
	cd sim && docker compose down -v --remove-orphans

sim-test:
	bash sim/scripts/sim-test.sh

test-m0:
	bash sim/scripts/test-m0.sh

accept-m0:
	bash sim/scripts/accept-m0.sh

test-fakes:
	bash sim/scripts/test-fakes.sh

test-m1:
	bash sim/scripts/test-m1.sh

test-render:
	bash sim/scripts/test-render.sh

test-local:
	bash sim/scripts/test-local.sh

accept-m1:
	bash sim/scripts/accept-m1.sh

test-favorites:
	bash sim/scripts/test-favorites.sh

test-audio:
	bash sim/scripts/test-audio.sh

test-m2:
	bash sim/scripts/test-m2.sh

accept-m2:
	bash sim/scripts/accept-m2.sh

soak-up:
	cd sim/soak && docker compose up -d --build --wait --wait-timeout 300

soak-down:
	cd sim/soak && docker compose down -v --remove-orphans

soak-status:
	python3 sim/soak/bin/soak-status.py

media:
	bash sim/scripts/gen-media.sh

test-jellyfin:
	bash sim/scripts/test-jellyfin.sh

test-screensaver:
	bash sim/scripts/test-screensaver.sh

test-fallback:
	bash sim/scripts/test-fallback.sh

accept-m3:
	bash sim/scripts/accept-m3.sh

accept-m4:
	bash sim/scripts/accept-m4.sh

accept-m5:
	bash sim/scripts/accept-m5.sh

test-m6:
	bash sim/scripts/test-m6.sh

accept-m6-setup:
	bash sim/scripts/accept-m6.sh setup

accept-m6-check:
	bash sim/scripts/accept-m6.sh check

test-combos:
	bash sim/scripts/test-combos.sh

test-real-retroarch:
	bash sim/scripts/test-real-retroarch.sh

test-pegasus-meta:
	bash sim/scripts/test-pegasus-meta.sh

test-remote-view:
	bash sim/scripts/test-remote-view.sh

test-saves-backup:
	bash sim/scripts/test-saves-backup.sh

test-cli-complete:
	bash sim/scripts/test-cli-complete.sh

test-provision:
	bash sim/scripts/test-provision.sh

test-p8:
	bash sim/scripts/test-p8.sh

test-stream-quality:
	bash sim/scripts/test-stream-quality.sh

test-webui-live:
	bash sim/scripts/test-webui-live.sh
