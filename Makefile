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
# Пути к движку и питону — абсолютные не для красоты: examples/ это симлинк на
# основную копию репозитория, и относительный ../../engine/audit.py из worktree
# приводил в движок ОСНОВНОЙ копии. Регресс при этом был зелёным, ничего не
# проверяя. Проверено на себе 28.08.2026.
regress:
	@cd examples/belgrade-1 && $(CURDIR)/$(VENV)/python $(CURDIR)/engine/audit.py score | head -2
	@cd examples/belgrade-2 && $(CURDIR)/$(VENV)/python $(CURDIR)/engine/audit.py score | head -2

demo:
	@echo "демо-набор поднимается блоком infra (задача T074)"
