.PHONY: check test regress demo lint types dead bounds fmt migrate cov-engine

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

# Демо-набор на английском (T074, T100): вымышленная точка, синтетический
# чек-лист demo/data, отчёт и письмо партнёру. Идемпотентно — повторный
# запуск возвращает демо к чистому виду, а не накапливает мусор.
#
# Состояние ложится в demo/state (каталог в .gitignore) или в DEMO_STATE_DIR,
# если он задан. Боевой STATE_DIR не трогается: сид его не читает намеренно —
# иначе эта цель, запущенная на сервере, писала бы в настоящие проверки.
demo:
	$(VENV)/python tools/seed_demo.py

# Накат схемы блока db (T091). DATABASE_URL берётся из .env — так же, как
# AUDIT_DATA_DIR для DATA выше. Идемпотентно: на уже накатанной базе печатает
# «нечего накатывать» и ничего не меняет.
migrate:
	$(VENV)/python -m src.db.migrate

cov-engine:  ## покрытие движка, который вызывается подпроцессом (T037)
	@rm -f .coverage.engine*
	@COVERAGE_PROCESS_START=$(CURDIR)/.coveragerc-engine \
	 COVERAGE_FILE=$(CURDIR)/.coverage.engine \
	 PYTHONPATH=$(CURDIR) \
	 $(VENV)/python -m pytest -q --no-cov -p no:cacheprovider tests/ > /dev/null
	@COVERAGE_FILE=$(CURDIR)/.coverage.engine $(VENV)/python -m coverage combine --rcfile=.coveragerc-engine > /dev/null
	@COVERAGE_FILE=$(CURDIR)/.coverage.engine $(VENV)/python -m coverage report --rcfile=.coveragerc-engine
