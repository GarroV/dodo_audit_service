.PHONY: check test test-honest regress demo demo-down loadcheck loadcheck-live fastpath processhint zonewords lint types dead bounds fmt migrate db-up db-down storage-up storage-down mcp cov-engine

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

# Прогон с ЧЕСТНОЙ проверкой порчей. Отдельная цель, а не режим `test`, и это
# измерено: `PYTHONDONTWRITEBYTECODE=1` заставляет каждый подпроцесс движка
# компилироваться заново, и вместе с покрытием набор перестаёт проходить —
# 115 секунд без покрытия против больше десяти минут с ним (замерено 03.09,
# регрессию внёс диспетчер и поймал на приёмке).
#
# Зачем переменная вообще: Python инвалидирует кэш байткода по паре «время
# правки в СЕКУНДАХ + размер». Порча, не меняющая размер и живущая меньше
# секунды, подбирает байткод предыдущего прогона, и тест проходит на сломанном
# коде — 12 промахов из 12. Опаснее обратный случай: восстановление после порчи
# тоже берёт кэш, и «тест поймал дефект» оказывается ложью.
#
# Поэтому: обычный прогон быстрый, а порчу гоняют этой целью или ставят
# переменную руками на конкретный файл.
test-honest:
	PYTHONDONTWRITEBYTECODE=1 $(VENV)/pytest --no-cov $(ARGS)

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
	@$(VENV)/python tools/regress_check.py

# Демо-набор на английском (T074, T100): вымышленная точка, синтетический
# чек-лист demo/data, отчёт и письмо партнёру. Идемпотентно — повторный
# запуск возвращает демо к чистому виду, а не накапливает мусор.
#
# Состояние ложится в demo/state (каталог в .gitignore) или в DEMO_STATE_DIR,
# если он задан. Боевой STATE_DIR не трогается: сид его не читает намеренно —
# иначе эта цель, запущенная на сервере, писала бы в настоящие проверки.
demo:
	$(VENV)/python tools/seed_demo.py

# Снос демо-стенда. Существует потому, что напрашивающаяся симметричная
# команда `docker compose --profile demo down -v` сносит ВМЕСТЕ С ДЕМО боевой
# бот и удаляет том `state` — состояние всех идущих проверок партнёров.
# Сервис без `profiles:` активен при любом `--profile`, а `-v` удаляет все
# именованные тома проекта. Поэтому здесь поимённо и без `-v`, а том демо
# удаляется отдельной строкой.
# Замер потолка нагрузки (T101, D058). Владелец назвал нагрузку главным риском,
# но целевого числа не задал — поэтому сначала измеряем потолок, потом решаем.
# Проверка способна упасть: со снятой блокировкой движка теряет записи альбома.
loadcheck:
	STATE_DIR=$${STATE_DIR:-/tmp/loadcheck-state} $(VENV)/python tools/loadcheck.py

# Живой замер (T101, D062): НАСТОЯЩИЕ вызовы модели, то есть платный прогон.
# Без --yes ничего не вызывает, только показывает план и цену. Владелец
# санкционировал: «нам надо реально понять ограничения системы».
loadcheck-live:
	$(VENV)/python tools/loadcheck_live.py $(ARGS)

# Замер T113: доля однозначных срабатываний быстрого пути (recognize.fastpath)
# на боевых данных examples/*/inspection.json. Детерминированно, без сети и
# без денег — гонять после каждого пополнения карты слов data/photo-cues.md.
fastpath:
	STATE_DIR=$${STATE_DIR:-/tmp/fastpath-state} $(VENV)/python tools/fastpath_measure.py

# Замер T166: сколько раз признак «<описание>, это <процесс>» срабатывает там,
# где указания словарю не было. Признак ненадёжен по устройству, и единственное,
# что о нём можно узнать, — доля ложных срабатываний. Корпус, на котором она
# считается по-настоящему (сырые слова аудитора, T183), копится в состоянии
# проверок: пока он пуст, замер говорит это прямо. Без сети и без денег.
processhint:
	$(VENV)/python tools/process_hint_measure.py

# Замер T242: узнаётся ли зона, названная НЕ в именительном падеже — так, как
# её называют на точке («на тепловом участке»). Имена берутся из ДЕЙСТВУЮЩЕЙ
# методики, формы порождаются правилами падежа и печатаются целиком: проверять
# их глазами — часть замера. Без сети и без денег.
#
# Вывод несёт боевые названия зон — в репозиторий его не класть (репозиторий
# публичный, `tests/test_methodology_leak.py`).
zonewords:
	STATE_DIR=$${STATE_DIR:-/tmp/zonewords-state} $(VENV)/python tools/zone_words_measure.py

demo-down:
	docker compose --profile demo rm -sf demo demo-seed
	-docker volume rm $$(docker compose --profile demo config --format json \
	  | $(VENV)/python -c "import json,sys; print(json.load(sys.stdin)['name']+'_demo-state')")

# Накат схемы блока db (T091). DATABASE_URL раннер читает из .env сам
# (`src/db/migrate.py`, load_env_file) — держать её в оболочке не нужно.
# Идемпотентно: на уже накатанной базе печатает «нечего накатывать» и ничего
# не меняет.
migrate:
	$(VENV)/python -m src.db.migrate

# Стенд базы одной командой (T090): поднять Postgres рядом с ботом, дождаться
# ГОТОВНОСТИ БАЗЫ (`--wait` идёт по healthcheck, а не по факту запуска
# контейнера — иначе накат упирается в ещё не открытый порт) и накатить схему
# с нуля. Идемпотентно: на поднятом стенде и накатанной схеме не делает ничего.
# Строка подключения при этом берётся из .env и должна указывать на этот же
# порт — см. POSTGRES_PORT и DATABASE_URL в .env.example.
db-up:
	docker compose --profile db up -d --wait db
	$(MAKE) migrate

# Снос стенда базы. Существует по той же причине, что demo-down: напрашивающаяся
# симметричная `docker compose --profile db down -v` удаляет ВСЕ именованные
# тома проекта, а не только тома профиля, — вместе с базой уносит `state`,
# состояние всех идущих проверок. Поэтому поимённо и без -v; том с данными
# базы остаётся и переживает пересоздание контейнера.
db-down:
	docker compose --profile db rm -sf db

# Хранилище кадров для локального смоука (T094). Не боевое хранилище: тома у
# него нет, данные не переживают пересоздание контейнера — боевое задаётся
# переменными S3_* и живёт снаружи.
storage-up:
	docker compose --profile storage up -d --wait storage

# Снос стенда хранилища — поимённо и без -v, по той же причине, что db-down:
# `--profile storage down -v` унёс бы том состояния идущих проверок.
storage-down:
	docker compose --profile storage rm -sf storage

# MCP-сервер поверх базы проверок (T095). Только чтение; доступ — личным
# токеном из MCP_TOKENS (.env), слушает петлю и наружу не публикуется.
# Строку подключения к базе и карту токенов читает сам из .env.
mcp:
	$(VENV)/python -m src.mcp

cov-engine:  ## покрытие движка, который вызывается подпроцессом (T037)
	@rm -f .coverage.engine*
	@COVERAGE_PROCESS_START=$(CURDIR)/.coveragerc-engine \
	 COVERAGE_FILE=$(CURDIR)/.coverage.engine \
	 COVERAGE_ENGINE_ROOT=$(CURDIR) \
	 PYTHONPATH=$(CURDIR) \
	 $(VENV)/python -m pytest -q --no-cov -p no:cacheprovider tests/ > /dev/null
	@COVERAGE_ENGINE_ROOT=$(CURDIR) COVERAGE_FILE=$(CURDIR)/.coverage.engine \
	 $(VENV)/python -m coverage combine --rcfile=.coveragerc-engine > /dev/null
	@COVERAGE_ENGINE_ROOT=$(CURDIR) COVERAGE_FILE=$(CURDIR)/.coverage.engine \
	 $(VENV)/python -m coverage report --rcfile=.coveragerc-engine
