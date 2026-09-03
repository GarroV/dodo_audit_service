"""Оснастка тестов методики: крошечный, но настоящий набор, по которому движок считает.

Методика здесь синтетическая — два пункта, две зоны — и это осознанно. Боевая
лежит вне git (D002), на чужой машине её может не быть, а каждая правка гоняет
движок тремя подпроцессами: на 136 пунктах прогон стоил бы минуты вместо
секунд. Всё, что проверяют тесты блока, на двух пунктах видно так же.

Набор именно настоящий, а не заглушка: `manage.py validate` его принимает, а
`audit.py score` по нему считает — иначе проверка «движок согласился» ничего
бы не проверяла.
"""

from __future__ import annotations

import json
from pathlib import Path

SCORING = {
    "start_pct": 100.0,
    "penalty": {"D1": 0.5, "D2": 2.0},
    "d3": {"mode": "zero_zone_share", "skip_other_violations_in_d3_zone": True},
    "cap_zone_loss_at_share": False,
    "floor_pct": 0.0,
    "grades": {
        "rules": [
            {"grade": "D", "if": {"min_d3": 1}, "label_ru": "Критическое", "label_en": "Critical"},
            {
                "grade": "A",
                "if": {"pct_at_least": 95.0, "max_d2": 0},
                "label_ru": "Стандарт",
                "label_en": "Meets",
            },
        ],
        "fallback": {"grade": "C", "label_ru": "Ниже", "label_en": "Below"},
    },
    "deadlines": {
        "max_days": {"D1": 14, "D2": 10, "D3": 0},
        "d3_note_ru": "немедленно",
        "d3_note_en": "immediately",
    },
    "plan_due_days": 10,
}

ЧЕКЛИСТ = (
    "id,kind,process_ru,process_en,question_ru,question_en,levels,zones,days\n"
    "CLN01,violation,Чистота,Cleanliness,Пол чистый,Floor is clean,D1;D2,fridge,10\n"
    "CLN02,violation,Чистота,Cleanliness,Стены чистые,Walls are clean,D1,*,10\n"
)

ЗОНЫ = "code,name_ru,name_en,share_pct\nfridge,Холодильник,Fridge,50\ndough,Тесто,Dough,50\n"

КРИТЕРИИ = (
    "# Критерии нарушений по вопросам\n"
    "\n## CLN01\nD1: пятна\nD2: слой грязи\n"
    "\n## CLN02\nD1: подтёки\n"
)


def build_methodology(where: Path) -> Path:
    """Разложить методику в указанном каталоге и вернуть его."""
    where.mkdir(parents=True, exist_ok=True)
    (where / "checklist.csv").write_text(ЧЕКЛИСТ, encoding="utf-8")
    (where / "zones.csv").write_text(ЗОНЫ, encoding="utf-8")
    (where / "criteria.md").write_text(КРИТЕРИИ, encoding="utf-8")
    (where / "scoring.json").write_text(json.dumps(SCORING, ensure_ascii=False), encoding="utf-8")
    return where
