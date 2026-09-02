# Образ бота выездных проверок (задача T070, блок infra).
#
# python:3.12-slim — Debian, не Alpine (решение D009 / docs/forge/plan.md,
# раздел «Стек»): musl-сборки Alpine не дают нужных системных библиотек
# Pango, на которых держится WeasyPrint.
FROM python:3.12-slim

# Системные зависимости слоем отдельно от кода — код меняется на каждый
# коммит, список пакетов почти никогда.
#
#  - libpango-1.0-0, libpangoft2-1.0-0, libharfbuzz-subset0 — рендерер PDF
#    (WeasyPrint 69.x, решение D009) кладёт текст через Pango/HarfBuzz.
#  - fonts-dejavu-core — кириллица (решение D009). Отчёт подключает свою
#    копию DejaVu Sans прямым путём из `engine/assets/fonts`
#    (`engine/report.py`, `@font-face … src: url("file://…")`), поэтому сам
#    шрифт в образ уже попадает вместе с кодом; системный пакет — фолбэк для
#    Pango/fontconfig на случай глифов вне явного `@font-face`, та же гарнитура.
#  - ffmpeg — перекодировка голосовых из OGG/Opus перед распознаванием
#    (решение D008): Telegram отдаёт формат, который OpenAI API не принимает
#    напрямую.
#  - tzdata — чтобы значение переменной TZ (задаётся в compose) вообще на
#    что-то указывало: без неё контейнер живёт по UTC, и даты в отчёте
#    партнёру уезжают на сутки относительно места проверки.
#  - procps — `pgrep` для healthcheck контейнера: у бота нет HTTP-порта
#    (long polling, решение D004), проверка живости идёт по процессу.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz-subset0 \
        fonts-dejavu-core \
        ffmpeg \
        tzdata \
        procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# `src/domain/config.py` считает корень проекта как `parents[2]` от своего
# файла и ищет `engine/audit.py` рядом с `src/` — оба каталога обязаны лежать
# сиблингами прямо под WORKDIR, как в самом репозитории.
COPY pyproject.toml ./
COPY engine ./engine
COPY src ./src
# Сид демо-набора (`tools/seed_demo.py`, задача T074) — тоже код продукта:
# демо-стенд из `docker-compose.yml` (профиль `demo`) запускает его этим же
# образом, а не отдельным. Синтетический чек-лист рядом не копируется: он
# монтируется снаружи, как и боевая методика, — тогда правка данных в `main`
# доезжает до демо без пересборки образа (задача T102).
COPY tools ./tools

# Editable-установка, а не обычная: обычный `pip install .` копирует пакет в
# site-packages, и `Path(__file__).resolve().parents[2]` из site-packages
# промахивается мимо `engine/` — движок физически лежит в /app, а не рядом с
# копией пакета. Editable-ссылка на /app сохраняет тот же путь для `__file__`,
# что и в репозитории, и заодно даёт единственный источник версий зависимостей
# — `pyproject.toml`, без второго списка в Dockerfile. `[dev]` сюда не идёт:
# ruff/mypy/pytest в проде не нужны и увеличивают образ.
RUN pip install --no-cache-dir --no-cache -e .

# Методика — данные управляющей компании (docs/forge/plan.md, «Данные
# предметной области лежат вне репозитория»). В образ не запекается: том
# `docker-compose.yml` монтирует её снаружи в /app/data, здесь для неё
# нарочно нет ни COPY, ни каталога-заглушки — пустой каталог маскировал бы
# отсутствие монтирования вместо явного отказа на старте
# (`domain.check_environment`).

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Здоровье процесса, а не HTTP-порта: бот не поднимает никакого сервера
# (plan.md, «bot → наружу. Только Telegram»). `python -m src.bot` в CMD — тот
# же процесс, что и в `pgrep`.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD pgrep -f "python -m src.bot" > /dev/null || exit 1

CMD ["python", "-m", "src.bot"]
