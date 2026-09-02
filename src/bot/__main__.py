"""Запуск бота: `python -m src.bot`.

`load_dotenv()` подставляет переменные из `.env` в окружение процесса до
чтения конфигурации. Без него эта же команда падала: `load_bot_settings`
смотрит только `os.environ`, а класть туда `.env` было некому — ни
`python-dotenv`, ни цель `run` в Makefile до сих пор не заводились (issue
#75). Внутри контейнера `.env` не запекается и не подкладывается: секреты
приходят через `docker compose` (`env_file`/`environment`,
`docker-compose.yml`), и там `load_dotenv()` просто не находит файла и не
делает ничего — переменные, которые уже стоят в окружении процесса, он не
трогает и не перетирает (`override=False` по умолчанию).
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

# Путь строится от файла, а не через поиск по cwd/стеку вызовов — тем же
# приёмом, что и корень проекта в `src/domain/config.py`
# (`Path(__file__).resolve().parents[2]`): предсказуемо при любом текущем
# каталоге запуска. Отсутствие файла (например, в контейнере — .env туда не
# кладётся) — не отказ: `load_dotenv` тихо возвращает False.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from .app import main  # noqa: E402 -- окружение должно быть прочитано до импорта конфигурации

main()
