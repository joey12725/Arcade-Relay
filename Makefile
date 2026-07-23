.PHONY: sim-up sim-down sim-test soak-up soak-down soak-status

sim-up:
	cd sim && docker compose up -d --build --wait --wait-timeout 300

sim-down:
	cd sim && docker compose down -v --remove-orphans

sim-test:
	bash sim/scripts/sim-test.sh

soak-up:
	cd sim/soak && docker compose up -d --build --wait --wait-timeout 300

soak-down:
	cd sim/soak && docker compose down -v --remove-orphans

soak-status:
	python3 sim/soak/bin/soak-status.py
