"""Идемпотентный сид демо-набора (задача T074, блок infra). Запускается `make demo`.

Собирает фиктивную проверку вымышленной точки «Demo Pizzeria #1» по
синтетическому чек-листу `demo/data/` (не боевая методика — она лежит в
`data/` и сюда не подмешивается) в изолированном состоянии `demo/state/`,
отдельном и от боевого `STATE_DIR`, и от `.state/` разработки.

Идемпотентность — не «не упадёт при повторном запуске», а «вернёт демо к
чистому виду»: скрипт стирает прежнюю демо-проверку этого чата целиком и
строит её заново тем же кодом, который использует бот (`src.domain`,
`src.report`), а не собственной копией логики — иначе расчёт оценки
разъехался бы с продуктом при первой же правке движка.

Демо целиком на английском (report_lang="en") — стандарт для демо-материалов
независимо от языка продукта; переменные окружения ставятся здесь и не берутся
из `.env`, чтобы демо не зависело от того, что в нём прописано для боевого
запуска, и не могло случайно тронуть боевое состояние.

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

DEMO_DATA_DIR = REPO_ROOT / "demo" / "data"
DEMO_STATE_DIR = REPO_ROOT / "demo" / "state"

# Ставится до импорта src.domain/src.report: обе точки читают окружение лениво
# при каждом вызове (`check_environment()`), но точка входа не должна ни на
# мгновение работать по чужому AUDIT_DATA_DIR/STATE_DIR, унаследованному из
# окружения процесса.
os.environ["AUDIT_DATA_DIR"] = str(DEMO_DATA_DIR)
os.environ["STATE_DIR"] = str(DEMO_STATE_DIR)

from src import domain  # noqa: E402 -- окружение выставляется до импорта
from src import report  # noqa: E402
from src.domain.engine import chat_dir  # noqa: E402

#: Заведомо вымышленный номер чата: реальные Telegram ID им не бывают
#: (слишком длинный, зарезервированный диапазон), коллизия с боевым чатом
#: исключена по построению, а не по соглашению.
DEMO_CHAT_ID = 999_000_000_001

#: Находки в порядке добавления: (код, класс, зона, формулировка).
#: Коды и зоны — из demo/data/checklist.csv и demo/data/zones.csv, не из
#: боевой методики.
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


def seed() -> Path:
    """Пересобрать демо-проверку с нуля и вернуть путь к собранному PDF."""
    settings = domain.check_environment()
    work_dir = chat_dir(DEMO_CHAT_ID, settings)
    if work_dir.exists():
        shutil.rmtree(work_dir)

    domain.start_inspection(
        DEMO_CHAT_ID,
        unit="Demo Pizzeria #1",
        kind="Planned",
        report_lang="en",
        city="Demo City",
        auditor="Demo Auditor",
    )
    for code, level, zone, evidence in DEMO_FINDINGS:
        domain.add_finding(DEMO_CHAT_ID, code, level, zone, evidence)

    score = domain.score(DEMO_CHAT_ID)
    pdf_path = report.build_pdf(DEMO_CHAT_ID, allow_missing_photos=True)

    print(f"Demo inspection seeded: chat_id={DEMO_CHAT_ID}, unit=Demo Pizzeria #1")
    print(f"Score: {score.pct:g}% grade {score.grade} ({score.label('en')})")
    print(f"State: {work_dir}")
    print(f"Report: {pdf_path}")
    return pdf_path


if __name__ == "__main__":
    seed()
