.PHONY: up down logs seed-demo evaluate test

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f

seed-demo:
	docker compose exec api python -m scripts.seed_demo_scenarios --scenario all

evaluate:
	docker compose exec api python -m scripts.evaluate

test:
	pytest tests/ -v
