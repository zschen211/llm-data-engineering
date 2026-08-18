.PHONY: help dev up down status logs

help:
	@echo "llm-data-engineering dev stack"
	@echo ""
	@echo "  make dev        start everything: infra (compose) + Ray + backends + frontend"
	@echo "  make up         alias of make dev"
	@echo "  make down       stop everything"
	@echo "  make status     per-service state + health probes"
	@echo "  make logs       tail all service logs (make logs asset|data_factory|frontend)"
	@echo ""
	@echo "under the hood: scripts/dev.sh (PID files + health waits under .run/)"

dev up:
	./scripts/dev.sh up

down:
	./scripts/dev.sh down

status:
	./scripts/dev.sh status

logs:
	./scripts/dev.sh logs
