.PHONY: check test regress demo lint types dead bounds fmt

VENV := ./.venv/bin
DATA := $(shell grep -E '^AUDIT_DATA_DIR=' .env 2>/dev/null | cut -d= -f2-)

# Полный набор проверок. Прогоняется перед сдачей блока.
check: fmt lint types test dead bounds

fmt:
	$(VENV)/ruff format --check .

lint:
	$(VENV)/ruff check .

types:
	$(VENV)/mypy

test:
	$(VENV)/pytest

dead:
	$(VENV)/vulture
	$(VENV)/deptry .

bounds:
	$(VENV)/lint-imports

# Регрессионный якорь: цифры на боевых данных не должны меняться.
# Ожидается: belgrade-1 → 97.5%, A, 5×D1 ; belgrade-2 → 97.0%, A, 6×D1
regress:
	@cd examples/belgrade-1 && python3 ../../engine/audit.py score | head -2
	@cd examples/belgrade-2 && python3 ../../engine/audit.py score | head -2

demo:
	@echo "демо-набор поднимается блоком infra (задача T074)"
