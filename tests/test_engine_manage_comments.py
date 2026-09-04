"""T168: строка-комментарий в методике — заявленная возможность, а не поломка.

`load_checklist()` пропускает строки, у которых `id` начинается с решётки,
намеренно и явно. `manage.py validate` об этом не знал и объявлял такую строку
сломанной («неизвестный kind»), то есть движок и его собственная проверка
расходились в трактовке одного и того же файла.

Чинить надо проверку, а не читателя: запрети комментарии в чтении — и они
перестанут работать там, где работают сегодня. Поэтому здесь закреплены обе
стороны: проверка молчит ровно на тех строках, которые движок пропускает, и
по-прежнему кричит на тех, что он бы прочитал.

Тесты идут на синтетической методике (`tests/methodology`), боевая не нужна.
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import ROOT, Run, run_engine

MANAGE = ROOT / "engine" / "manage.py"
AUDIT = ROOT / "engine" / "audit.py"

#: Ровно так комментарий и пишут: пустая строка-разделитель и строка с текстом.
КОММЕНТАРИИ = ("#", "#набор иллюстративный, боевая методика лежит вне git")


def дописать_строки(data_dir: Path, ids: tuple[str, ...]) -> None:
    """Дописать в чек-лист строки с заданным `id` и пустыми остальными клетками."""
    path = data_dir / "checklist.csv"
    with path.open(encoding="utf-8-sig") as f:
        fields = next(iter(csv.reader(f)))
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writerows({"id": x} for x in ids)


def строк_с_вопросами(data_dir: Path) -> int:
    """Сколько строк чек-листа движок прочитает как вопросы."""
    with (data_dir / "checklist.csv").open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return len([r for r in rows if (r.get("id") or "").strip() and not r["id"].startswith("#")])


@pytest.fixture
def validate(data_copy: Path, workdir: Path) -> Callable[[], Run]:
    """`manage.py validate` на копии методики. Боевой `data/` не трогается."""

    def call() -> Run:
        return run_engine(
            MANAGE, "validate", cwd=workdir, env_extra={"CHECKLIST_DIR": str(data_copy)}
        )

    return call


def test_методика_с_комментарием_проходит_проверку(
    data_copy: Path, validate: Callable[[], Run]
) -> None:
    дописать_строки(data_copy, КОММЕНТАРИИ)
    r = validate()
    assert r.code == 0, f"комментарий объявлен поломкой:\n{r.text}"
    assert "Всё в порядке" in r.out


def test_комментарий_не_считается_вопросом(data_copy: Path, validate: Callable[[], Run]) -> None:
    """Счётчик проверки — это то, что прочитает движок, а не число строк в файле."""
    было = строк_с_вопросами(data_copy)
    дописать_строки(data_copy, КОММЕНТАРИИ)
    r = validate()
    assert r.code == 0, r.text
    assert f"вопросов: {было}" in r.out, f"комментарии попали в счётчик вопросов:\n{r.out}"


def test_отступ_перед_решёткой_читается_так_же(
    data_copy: Path, validate: Callable[[], Run]
) -> None:
    """Читатель обрезает пробелы до проверки на решётку — проверка обязана тоже."""
    дописать_строки(data_copy, ("   # с отступом",))
    r = validate()
    assert r.code == 0, f"строка с отступом перед решёткой объявлена поломкой:\n{r.text}"


def test_проверка_и_движок_видят_один_и_тот_же_файл(
    data_copy: Path, workdir: Path, validate: Callable[[], Run]
) -> None:
    """Главное расхождение T168: проверка красная, а движок ту же строку читает молча."""
    дописать_строки(data_copy, КОММЕНТАРИИ)
    r = run_engine(
        AUDIT, "index", "--q", "набор", cwd=workdir, env_extra={"CHECKLIST_DIR": str(data_copy)}
    )
    assert r.code == 0, r.text
    assert "набор иллюстративный" not in r.out, "комментарий предложен аудитору как пункт"
    assert validate().code == 0, "движок строку пропускает, а его же проверка её не принимает"


def test_настоящая_поломка_рядом_с_комментарием_видна(
    data_copy: Path, validate: Callable[[], Run]
) -> None:
    """Послабление не должно глушить строку, которую движок ПРОЧИТАЕТ."""
    дописать_строки(data_copy, (*КОММЕНТАРИИ, "ZZZ01"))
    r = validate()
    assert r.code != 0, "строка без вида записи прошла проверку молча"
    assert "ZZZ01" in r.text, f"сломанная строка не названа:\n{r.text}"
    assert "#" not in r.text.split("Проблемы:")[-1], f"комментарий назван проблемой:\n{r.text}"


def test_комментарий_переживает_правку_методики(
    data_copy: Path, workdir: Path, validate: Callable[[], Run]
) -> None:
    """Возможность живёт, только если правка через `manage.py` её не стирает."""
    дописать_строки(data_copy, КОММЕНТАРИИ)
    r = run_engine(
        MANAGE,
        "edit",
        "CLN05",
        "--days",
        "7",
        cwd=workdir,
        env_extra={"CHECKLIST_DIR": str(data_copy)},
    )
    assert r.code == 0, r.text
    текст = (data_copy / "checklist.csv").read_text(encoding="utf-8")
    for c in КОММЕНТАРИИ:
        assert c in текст, f"правка методики стёрла комментарий «{c}»"
    assert validate().code == 0, r.text
