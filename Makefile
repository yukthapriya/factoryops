# Local (non-Docker) workflow. Assumes PostgreSQL on localhost:5432 and uv + Node installed.
DB_URL ?= postgresql+psycopg://localhost:5432/factoryops
export FACTORYOPS_DATABASE_URL ?= $(DB_URL)

.PHONY: setup db migrate seed register-fleet ai4i api consumer simulate simulate-direct frontend test lint up down

setup:            ## install backend + frontend dependencies
	cd backend && uv sync
	cd frontend && npm ci

db:               ## create local dev + test databases (idempotent)
	-createdb factoryops
	-createdb factoryops_test

migrate:          ## apply Alembic migrations
	cd backend && uv run alembic upgrade head

seed:             ## reset and backfill 6h of deterministic synthetic history
	cd backend && uv run python -m factoryops.cli seed --reset --hours 6 --seed 42

register-fleet:   ## register the 15 simulated units without history (idempotent)
	cd backend && uv run python -m factoryops.cli register-fleet

ai4i:             ## download, verify, import and evaluate the UCI AI4I 2020 benchmark
	cd backend && uv run python -m factoryops.cli ai4i-download --dest data
	cd backend && uv run python -m factoryops.cli ai4i-import --file data/ai4i2020.csv
	cd backend && uv run python -m factoryops.cli ai4i-evaluate --seed 42

api:              ## run the API with reload on :8000
	cd backend && uv run uvicorn factoryops.main:app --reload --port 8000

consumer:         ## run the Kafka consumer
	cd backend && uv run python -m factoryops.consumer

simulate:         ## stream telemetry to Kafka
	cd backend && uv run python -m factoryops.simulator --transport kafka --scenario mixed --forever

simulate-direct:  ## stream telemetry through the pipeline without Kafka
	cd backend && uv run python -m factoryops.simulator --transport direct --scenario mixed --interval 2 --forever

frontend:         ## run the Vite dev server on :5173 (proxies /api and /ws to :8000)
	cd frontend && npm run dev

test:             ## all lint/type/test/build checks
	./scripts/test.sh

up:               ## full Docker stack
	docker compose up --build

down:
	docker compose down
