"""T021 и T025: состояние проверки — папка на чат и единственный слой доступа.

Состояние живёт в файле (решение D007), значит альбом из нескольких кадров
пишется параллельно и запись обязана быть атомарной. Три языка — интерфейса,
речи аудитора и отчёта — хранятся раздельно: аудитор ведёт бота по-русски,
говорит по-сербски, а отчёт партнёру уходит на английском.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.domain import (
    Inspection,
    add_finding,
    attach_photo,
    checklist_version,
    get_state,
    start_inspection,
)
from src.domain.errors import DomainError, EngineError, ValidationError


def начать(chat_id: int = 42, **kw: str) -> Inspection:
    params: dict[str, str] = {
        "unit": "Белград-1",
        "kind": "planned",
        "report_lang": "ru",
    }
    params.update(kw)
    return start_inspection(chat_id, **params)


def файл_состояния(state_dir: Path, chat_id: int = 42) -> Path:
    return state_dir / f"chat_{chat_id}" / "inspection.json"


def test_до_старта_проверки_состояния_нет(domain_env: Path) -> None:
    assert get_state(42) is None


def test_старт_создаёт_папку_на_чат_и_файл_состояния(domain_env: Path) -> None:
    начать()
    assert файл_состояния(domain_env).is_file()
    состояние = get_state(42)
    assert состояние is not None
    assert состояние.unit == "Белград-1"
    assert состояние.kind == "planned"
    assert состояние.chat_id == 42


def test_проверки_разных_чатов_не_смешиваются(domain_env: Path) -> None:
    начать(1, unit="Белград-1")
    начать(2, unit="Белград-2")
    add_finding(1, code="CLN05", level="D1", zone="hot_kitchen", text="нагар")
    первая = get_state(1)
    вторая = get_state(2)
    assert первая is not None and вторая is not None
    assert первая.unit == "Белград-1" and вторая.unit == "Белград-2"
    assert len(первая.findings) == 1, "запись ушла не в тот чат"
    assert вторая.findings == [], "запись видна в чужой проверке"


def test_три_языка_хранятся_раздельно(domain_env: Path) -> None:
    """Ни один язык не выведен из другого — это три поля, а не одно."""
    начать(report_lang="en", ui_lang="ru", speech_lang="sr")
    состояние = get_state(42)
    assert состояние is not None
    assert (состояние.ui_lang, состояние.speech_lang, состояние.report_lang) == ("ru", "sr", "en")


def test_язык_отчёта_доезжает_до_движка(domain_env: Path) -> None:
    """Отчёт собирает движок по своему полю — значит язык обязан лежать там."""
    начать(report_lang="en")
    meta = json.loads(файл_состояния(domain_env).read_text(encoding="utf-8"))["meta"]
    assert meta["lang"] == "en"


def test_язык_отчёта_вне_методики_отклоняется(domain_env: Path) -> None:
    """Отказ идёт ДО движка (T152): на этом языке нечем назвать даже вид проверки."""
    with pytest.raises(ValidationError) as e:
        начать(report_lang="sr")
    assert "sr" in str(e.value)
    assert get_state(42) is None, "проверка всё-таки начата с языком, которого нет в отчёте"


def test_версия_чек_листа_записана_в_проверку(domain_env: Path) -> None:
    """Отчёт годичной давности должен сходиться — значит версия лежит в проверке."""
    начать()
    состояние = get_state(42)
    assert состояние is not None
    assert состояние.checklist_version == checklist_version()
    assert состояние.checklist_version


def test_отказ_движка_не_проглатывается(domain_env: Path) -> None:
    """Пустая пиццерия — отказ движка, и наружу он должен уйти текстом."""
    with pytest.raises(EngineError) as e:
        начать(unit="   ")
    assert "пиццери" in str(e.value).lower(), f"отказ пересказан невнятно: {e.value}"


def test_поля_блока_переживают_команды_движка(domain_env: Path) -> None:
    """`audit.py add` переписывает файл целиком — языки и версия обязаны уцелеть."""
    начать(report_lang="en", ui_lang="ru", speech_lang="sr")
    add_finding(42, code="CLN05", level="D1", zone="hot_kitchen", text="нагар")
    состояние = get_state(42)
    assert состояние is not None
    assert (состояние.ui_lang, состояние.speech_lang, состояние.report_lang) == ("ru", "sr", "en")
    assert состояние.checklist_version == checklist_version()
    assert len(состояние.findings) == 1


def test_форма_состояния_готова_к_мультиарендности(domain_env: Path) -> None:
    """D005: форма данных сейчас, функции потом — арендатор лежит в проверке."""
    начать()
    состояние = get_state(42)
    assert состояние is not None
    assert состояние.tenant == "default"
    assert начать(7, tenant="dodo-rs").tenant == "dodo-rs"


def test_битое_состояние_это_отказ_а_не_пустая_проверка(domain_env: Path) -> None:
    начать()
    файл_состояния(domain_env).write_text("{это не json", encoding="utf-8")
    with pytest.raises(DomainError) as e:
        get_state(42)
    assert "inspection.json" in str(e.value), f"не сказано, какой файл испорчен: {e.value}"


def test_параллельная_дозагрузка_кадров_альбома_ничего_не_теряет(domain_env: Path) -> None:
    """Альбом приходит несколькими кадрами разом — запись поверх файла их теряла."""
    начать()
    add_finding(42, code="CLN05", level="D1", zone="hot_kitchen", text="нагар")
    кадры = [f"AgACAgIAAxkBAAI{i:04d}" for i in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda fid: attach_photo(42, 1, fid), кадры))
    состояние = get_state(42)
    assert состояние is not None
    запись = состояние.finding(1)
    assert запись is not None
    assert sorted(запись.photos) == sorted(кадры), "кадры потерялись при одновременной записи"


def test_параллельная_фиксация_записей_не_бьёт_файл(domain_env: Path) -> None:
    начать()
    зоны = ["hot_kitchen", "cold_kitchen", "dining", "fridge", "dough", "staff"]
    with ThreadPoolExecutor(max_workers=len(зоны)) as pool:
        list(
            pool.map(
                lambda z: add_finding(42, code="CLN06", level="D1", zone=z, text="загрязнение"),
                зоны,
            )
        )
    сырое = файл_состояния(domain_env).read_text(encoding="utf-8")
    json.loads(сырое)  # битый JSON здесь и есть провал теста
    состояние = get_state(42)
    assert состояние is not None
    assert len(состояние.findings) == len(зоны), "часть записей потеряна"
    assert len({f.n for f in состояние.findings}) == len(зоны), "номера записей повторились"
