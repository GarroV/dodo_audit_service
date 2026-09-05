"""Карта кадров демо: демо доходит до записи на первом же комментарии (T150, #121).

Демо-набор — это методика целиком, а не её половина. Обязательных файлов
(`checklist.csv`, `zones.csv`, `scoring.json`, `criteria.md`) у него четыре, и
они есть; необязательной карты кадров (`photo-cues.md`) не было вовсе. Пока
отсутствие карты было отказом, демо умирало на первом комментарии; отказ
вылечен (T157, D068) — карта нужна не поэтому.

**Без карты в демо не работает быстрый путь** (D063, D064): сверка со списком
нарушений — единственное место, где запись появляется БЕЗ вызова модели.
Демо-стенд ходит наружу под чужим ключом или без ключа вовсе, и на показе это
разница между «бот ответил сразу» и «модель недоступна, выберите пункт руками
из десяти кнопок». Поэтому карта демо проверяется не наличием файла, а тем,
что на демо-комментарии она доводит разговор до записи и модель при этом не
зовётся ни разу.

**Демо-объекты и демо-коды.** Демо лежит В git, боевая методика намеренно вне
его (D002, D073), поэтому в карту демо не переносится ни одна боевая
формулировка — за этим отдельно следит `tests/test_methodology_leak.py`, а
англоязычность всего набора сторожит `tests/test_demo_seed.py`.
"""

from __future__ import annotations

import csv
import subprocess
from pathlib import Path

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    feed,
    make_bot,
    photo_message,
    stub_classify,
    text_message,
)
from bot_harness import callback_query as callback

from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.keyboards import KIND_PREFIX, LANG_PREFIX, NEW_INSPECTION_CALLBACK
from src.domain import get_state
from src.recognize.config import NO_CHAT
from src.recognize.cues import load_cues
from src.recognize.errors import ModelUnavailable
from src.recognize.fastpath import fast_path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DATA = ROOT / "demo" / "data"
DEMO_CUES = DEMO_DATA / "photo-cues.md"

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Демо-комментарий, на котором быстрый путь обязан сработать. Зона названа
#: словом «entrance» — это имя демо-зоны `facade` из `demo/data/zones.csv`, а
#: не догадка кода. Строка карты, которую он покрывает, ведёт в `DEM02`:
#: единственный класс `D1`, зоны `facade` и `dining` — в названной зоне
#: остаётся ровно один пункт.
DEMO_NOTE = "Fingerprints and dust on the entrance door glass"

#: Что обязано получиться из `DEMO_NOTE`. Код и класс — из
#: `demo/data/checklist.csv`, а не из головы: пункт с единственным классом там
#: один на зону, и подмена любого из трёх значений ломает показ.
DEMO_PICK = ("DEM02", "D1", "facade")


def _demo_codes() -> set[str]:
    with (DEMO_DATA / "checklist.csv").open(encoding="utf-8-sig") as fh:
        return {row["id"].strip() for row in csv.DictReader(fh) if (row.get("id") or "").strip()}


@pytest.fixture
def demo_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Окружение демо-стенда: методика демо, состояние во временной папке.

    Ровно то, что ставит профиль `demo` в `docker-compose.yml`, — включая
    `BOT_UI_LANG=en`: до начала проверки язык интерфейса взять больше неоткуда,
    и без него демо здоровается по-русски (T131).
    """
    state = tmp_path / "state"
    monkeypatch.setenv("AUDIT_DATA_DIR", str(DEMO_DATA))
    monkeypatch.setenv("STATE_DIR", str(state))
    monkeypatch.setenv("BOT_UI_LANG", "en")
    monkeypatch.chdir(tmp_path)
    return state


# --- сам файл карты ----------------------------------------------------------


def test_карта_кадров_демо_лежит_в_git() -> None:
    """Файл на диске — не то же самое, что файл в репозитории.

    Весь демо-набор уже терялся именно так: правило `data/` в `.gitignore`
    съедало `demo/data/`, файлы жили в рабочей копии автора, и приёмка этого не
    видела (журнал блока infra, волна 2).
    """
    tracked = subprocess.run(
        ["git", "ls-files", "demo/data/photo-cues.md"],  # noqa: S607 — git из PATH
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    assert tracked == ["demo/data/photo-cues.md"], (
        "карты кадров демо нет в репозитории — на стенде её не окажется, "
        "и быстрый путь в демо не сработает ни разу"
    )


def test_карта_демо_разбирается_и_ведёт_только_в_демо_коды() -> None:
    """Разбирается тем же кодом, что и боевая, и не знает чужих пунктов."""
    cues = load_cues(DEMO_CUES, chat_id=NO_CHAT)
    assert cues, f"из {DEMO_CUES} не разобрано ни одной строки — формат таблицы не тот"
    codes = {code for cue in cues for code in cue.codes}
    assert codes <= _demo_codes(), (
        f"карта демо ведёт в пункты, которых нет в demo/data/checklist.csv: "
        f"{sorted(codes - _demo_codes())}"
    )


def test_каждый_пункт_демо_назван_картой() -> None:
    """Карта покрывает весь демо-чек-лист, а не три пункта из десяти.

    Иначе показ упирается в модель на любом комментарии, кроме заготовленного,
    и демо снова зависит от чужого ключа.
    """
    covered = {code for cue in load_cues(DEMO_CUES, chat_id=NO_CHAT) for code in cue.codes}
    missing = sorted(_demo_codes() - covered)
    assert not missing, f"карта демо не называет пункты {missing}"


# --- быстрый путь на демо-наборе ---------------------------------------------


def test_быстрый_путь_срабатывает_на_демо_комментарии(demo_env: Path) -> None:
    """Главное требование задачи: на демо-наборе сверка доводит до пункта сама."""
    found = fast_path(DEMO_NOTE, DEMO_PICK[2], lang="en", chat_id=NO_CHAT)
    assert found.item is not None, (
        f"быстрый путь на демо-наборе не сработал ({found.reason}) — "
        f"показ уйдёт в модель на первом же комментарии"
    )
    assert (found.item.code, found.item.level, found.item.zone) == DEMO_PICK
    assert found.item.cue, "строка карты не названа — аудитору не видно, почему предложен пункт"


# --- разговор целиком --------------------------------------------------------


def _состояние_на_диске(state_dir: Path) -> None:
    """Диск читается синхронной функцией нарочно: ASYNC240 запрещает трогать
    `pathlib` внутри `async def` (тот же приём, что в `test_e2e_inspection`)."""
    assert (state_dir / f"chat_{CHAT_ID}" / "inspection.json").is_file(), (
        f"проверка демо легла мимо состояния стенда: {sorted(state_dir.glob('*'))}"
    )


@pytest.mark.asyncio
async def test_демо_доходит_до_записи_без_модели(
    demo_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Приветствие → комментарий → запись, и модель не зовётся ни разу.

    Модель подменена отказом намеренно: демо-стенд поднимают без боевого ключа,
    и это его штатное состояние. Пустой список вызовов — доказательство того,
    что запись легла сверкой, а не разбором.
    """
    asked = stub_classify(monkeypatch, ModelUnavailable("демо-стенд без ключа"))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message("/start"))
    greeting = session.last_text
    await feed(dp, bot, callback(NEW_INSPECTION_CALLBACK))
    await feed(dp, bot, text_message("Demo Pizzeria #1"))
    await feed(dp, bot, callback(f"{KIND_PREFIX}planned"))
    await feed(dp, bot, callback(f"{LANG_PREFIX}en"))
    await feed(dp, bot, photo_message("demo-frame-1", caption=DEMO_NOTE))

    _состояние_на_диске(demo_env)
    state = get_state(CHAT_ID)
    assert state is not None, "мастер начала проверки не довёл демо до проверки"
    assert len(state.findings) == 1, (
        f"на первом же комментарии демо записи не сделало: {session.texts[-2:]}"
    )
    saved = state.findings[0]
    assert (saved.code, saved.level, saved.zone) == DEMO_PICK
    assert saved.text == DEMO_NOTE, "формулировку записи кто-то сочинил за аудитора"
    assert asked == [], "модель звали, хотя карта демо обязана была ответить сама"
    assert greeting.strip(), "демо не поздоровалось"
