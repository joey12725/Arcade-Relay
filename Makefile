.PHONY: media sim-up sim-down sim-test test-m0 accept-m0 test-fakes test-m1 soak-up soak-down soak-status

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
