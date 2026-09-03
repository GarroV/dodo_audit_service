"""Правила `.gitignore` для боевых данных — регресс на оба направления сразу.

Репозиторий публичный (D001), а рядом с кодом лежат методика управляющей
компании (`data/`) и боевые проверки партнёров (`examples/`). Правило, которое
их закрывает, ломалось уже дважды и оба раза молча:

1. `data/` без ведущей косой совпадало с ЛЮБЫМ каталогом `data` на любой
   глубине и съело весь синтетический набор `demo/data/` — он не попал в
   репозиторий ни одним коммитом, `make demo` не работал ни у кого, и приёмка
   этого не увидела.
2. `/data/` с хвостовой косой совпадает только с настоящим каталогом и НЕ
   совпадает с символической ссылкой — а в рабочие копии блоков данные
   подкладываются в том числе симлинком. `git add data` на таком симлинке
   проходит успешно и уносит в публичный репозиторий абсолютный путь с машины
   автора.

Оба направления проверяются здесь на настоящем `.gitignore` проекта, взятом
файлом, а не переписанном в тест: тест на копии правил доказывал бы только то,
что копия верна.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GITIGNORE = ROOT / ".gitignore"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — аргументы собираем сами, ввода извне нет
        ["git", *args],  # noqa: S607 — git берётся из PATH намеренно, как во всём проекте
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Изолированный репозиторий с ЖИВЫМИ правилами проекта.

    Настоящий репозиторий для этого не годится: проверять надо поведение на
    симлинке, а подкладывать симлинк в рабочее дерево проекта ради теста —
    ровно тот риск, от которого правило и защищает.
    """
    repo = tmp_path / "repo"
    (repo / "demo" / "data").mkdir(parents=True)
    (repo / "examples" / "belgrade-1").mkdir(parents=True)
    (repo / "elsewhere").mkdir()
    shutil.copy(GITIGNORE, repo / ".gitignore")
    (repo / "demo" / "data" / "checklist.csv").write_text("id,q\n", encoding="utf-8")
    (repo / "examples" / "belgrade-1" / "inspection.json").write_text("{}", encoding="utf-8")
    (repo / "elsewhere" / "checklist.csv").write_text("боевая методика\n", encoding="utf-8")
    _git(repo, "init", "-q", ".")
    return repo


def _ignored(repo: Path, path: str) -> bool:
    return _git(repo, "check-ignore", "-q", path).returncode == 0


def test_боевая_методика_каталогом_закрыта(repo: Path) -> None:
    (repo / "data").mkdir()
    (repo / "data" / "checklist.csv").write_text("боевая методика\n", encoding="utf-8")
    assert _ignored(repo, "data"), (
        "каталог data/ открыт — методика УК уедет в публичный репозиторий"
    )


def test_боевая_методика_симлинком_тоже_закрыта(repo: Path) -> None:
    """Правило со слэшем на конце симлинк не ловит — нужна пара без слэша."""
    (repo / "data").symlink_to(repo / "elsewhere", target_is_directory=True)
    assert _ignored(repo, "data"), (
        "симлинк data открыт: git add data уносит в публичный репозиторий "
        "абсолютный путь с машины автора"
    )
    added = _git(repo, "add", "data")
    assert added.returncode != 0, f"git add симлинка прошёл: {added.stderr or added.stdout}"


def test_боевые_проверки_симлинком_закрыты(repo: Path) -> None:
    (repo / "examples-real").mkdir()
    shutil.rmtree(repo / "examples")
    (repo / "examples").symlink_to(repo / "examples-real", target_is_directory=True)
    assert _ignored(repo, "examples"), "симлинк examples открыт — боевые проверки партнёров"


def test_демо_набор_в_репозитории(repo: Path) -> None:
    """Встречное утверждение: правило не должно съесть demo/data, как в T074."""
    assert not _ignored(repo, "demo/data/checklist.csv"), (
        "demo/data закрыт правилом — синтетический набор снова не попадёт в репозиторий, "
        "и make demo не заработает ни у кого, кроме автора"
    )
