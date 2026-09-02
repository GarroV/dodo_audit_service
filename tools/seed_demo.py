"""Идемпотентный сид демо-набора (задачи T074, T100; блок infra). Запускается `make demo`.

Собирает фиктивную проверку вымышленной точки «Demo Pizzeria #1» по
синтетическому чек-листу `demo/data/` (не боевая методика — она лежит в
`data/` и сюда не подмешивается), считает оценку, собирает отчёт и письмо
партнёру.

Идемпотентность — не «не упадёт при повторном запуске», а «вернёт демо к
чистому виду»: скрипт стирает прежнюю демо-проверку этого чата целиком и
строит её заново тем же кодом, который использует бот (`src.domain`,
`src.report`), а не собственной копией логики — иначе расчёт оценки
разъехался бы с продуктом при первой же правке движка.

Демо целиком на английском — стандарт для демо-материалов независимо от языка
продукта (конституция, раздел «Демо-режим»). Английский здесь не только у
отчёта: язык интерфейса и язык речи аудитора тоже ставятся `en`, иначе бот на
демо-пути заговорит по-русски — он берёт язык интерфейса из состояния
проверки. Отдельного `--lang en` сборщику отчёта не передаётся намеренно:
язык отчёта уже записан в шапке проверки (`report_lang`), движок читает его
оттуда (`lang = --lang или meta.lang`), и вторая запись того же факта
разъехалась бы с первой.

**Где живёт состояние демо.** В `DEMO_STATE_DIR`, а не в `STATE_DIR`. Это
осознанно разные переменные: `STATE_DIR` — путь работающего стенда, и на
сервере он указывает на настоящие проверки. Читай сид его — `make demo`,
запущенный на стенде, положил бы демо-проверку рядом с боевыми, а собранный
демо-отчёт лёг бы в одну папку с отчётами партнёрам. Демо-стенд из
`docker-compose.yml` ставит обе переменные в один и тот же путь своего тома —
там они совпадают, и это видно в файле, а не подразумевается.

Запуск (из корня репозитория):

    .venv/bin/python tools/seed_demo.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src import domain, report  # noqa: E402 -- путь к репозиторию выставляется выше
from src.domain.engine import chat_dir  # noqa: E402

#: Синтетический чек-лист демо. Лежит в репозитории и в образ не запекается —
#: демо-стенд монтирует его так же, как боевой стенд монтирует `data/`.
DEMO_DATA_DIR = REPO_ROOT / "demo" / "data"

#: Куда класть состояние демо, если переменная не задана. Каталог в
#: `.gitignore`: собранный отчёт — артефакт сида, и один раз он уже уехал в
#: публичный репозиторий (коммит f6aa577).
DEFAULT_DEMO_STATE_DIR = REPO_ROOT / "demo" / "state"

#: Имя переменной окружения — своё, не `STATE_DIR` (почему — в шапке модуля).
DEMO_STATE_DIR_VAR = "DEMO_STATE_DIR"

#: Заведомо вымышленный номер чата: реальные Telegram ID им не бывают
#: (слишком длинный, зарезервированный диапазон), коллизия с боевым чатом
#: исключена по построению, а не по соглашению.
DEMO_CHAT_ID = 999_000_000_001

#: Письмо партнёру рядом с отчётом: это тоже материал демо, и без файла его
#: язык нечем проверить.
LETTER_NAME = "letter.txt"

#: Находки в порядке добавления: (код, класс, зона, формулировка).
#: Коды и зоны — из demo/data/checklist.csv и demo/data/zones.csv, не из
#: боевой методики. Состав подобран так, чтобы демо показывало разбор с
#: вычетами, а не плоское «всё хорошо»: 3×D1 + 1×D2 дают 92 % и класс B.
DEMO_FINDINGS = (
    ("DEM01", "D1", "facade", "One bin lid propped open near the entrance, no overflow."),
    ("DEM03", "D1", "dining", "Scratch and dried stain on one guest table near the window."),
    ("DEM06", "D2", "kitchen", "Dried sauce splashes on the prep counter by the oven."),
    (
        "DEM07",
        "D1",
        "storage",
        "Two boxes of dough missing a received-date label on the top shelf.",
    ),
)


def demo_state_dir(env: dict[str, str] | None = None) -> Path:
    """Каталог состояния демо: `DEMO_STATE_DIR` или `demo/state` в репозитории."""
    src = os.environ if env is None else env
    raw = (src.get(DEMO_STATE_DIR_VAR) or "").strip()
    if not raw:
        return DEFAULT_DEMO_STATE_DIR
    # abspath, а не resolve(): каталог демо-стенда — том контейнера, и
    # разворачивать симлинки означало бы печатать путь, которого человек у
    # себя не увидит (тем же приёмом живёт src/domain/config.py).
    return Path(os.path.abspath(os.path.expanduser(raw)))


def seed() -> Path:
    """Пересобрать демо-проверку с нуля и вернуть путь к собранному PDF."""
    # Ставится до первого обращения к domain/report: обе точки читают
    # окружение лениво при каждом вызове (`check_environment()`), но ни один
    # вызов не должен ни на мгновение уйти по чужому пути, унаследованному из
    # окружения процесса.
    state_dir = demo_state_dir()
    os.environ["AUDIT_DATA_DIR"] = str(DEMO_DATA_DIR)
    os.environ["STATE_DIR"] = str(state_dir)

    settings = domain.check_environment()
    work_dir = chat_dir(DEMO_CHAT_ID, settings)
    if work_dir.exists():
        shutil.rmtree(work_dir)

    domain.start_inspection(
        DEMO_CHAT_ID,
        unit="Demo Pizzeria #1",
        kind="Planned",
        report_lang="en",
        ui_lang="en",
        speech_lang="en",
        city="Demo City",
        auditor="Demo Auditor",
    )
    for code, level, zone, evidence in DEMO_FINDINGS:
        domain.add_finding(DEMO_CHAT_ID, code, level, zone, evidence)

    score = domain.score(DEMO_CHAT_ID)
    pdf_path = report.build_pdf(DEMO_CHAT_ID, allow_missing_photos=True)
    letter_path = work_dir / LETTER_NAME
    letter_path.write_text(report.build_letter(DEMO_CHAT_ID) + "\n", encoding="utf-8")

    print(f"Demo inspection seeded: chat_id={DEMO_CHAT_ID}, unit=Demo Pizzeria #1")
    print(f"Score: {score.pct:g}% grade {score.grade} ({score.label('en')})")
    print(f"State: {work_dir}")
    print(f"Report: {pdf_path}")
    print(f"Letter: {letter_path}")
    return pdf_path


if __name__ == "__main__":
    seed()
