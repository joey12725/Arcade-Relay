.PHONY: sim-up sim-down sim-test

sim-up:
	cd sim && docker compose up -d --build --wait --wait-timeout 300

sim-down:
	cd sim && docker compose down -v --remove-orphans

sim-test:
	bash sim/scripts/sim-test.sh
