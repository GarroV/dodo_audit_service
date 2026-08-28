"""Общая оснастка тестов движка.

Движок запускается подпроцессом — так же, как его будет звать `domain` (см.
`docs/forge/plan.md`, раздел «Архитектура»). Импортировать его в тесты нельзя:
контракт import-linter запрещает импорт `engine` из кода продукта, и тесты не
должны учить обходить это правило.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AUDIT = ROOT / "engine" / "audit.py"
REPORT = ROOT / "engine" / "report.py"
DATA = ROOT / "data"
EXAMPLES = ROOT / "examples"

# Причина пропуска одна на все тесты: методика и боевые проверки лежат вне git
# (решение D002), поэтому на чужой машине их может не быть.
NO_DATA = not (DATA / "checklist.csv").exists()
NO_EXAMPLES = not (EXAMPLES / "belgrade-1" / "inspection.json").exists()

requires_data = pytest.mark.skipif(
    NO_DATA, reason="нет data/checklist.csv — методика вне git (D002)"
)
requires_examples = pytest.mark.skipif(
    NO_EXAMPLES, reason="нет examples/belgrade-1 — боевые проверки вне git (D002)"
)


@dataclass(frozen=True)
class Run:
    """Результат запуска команды движка."""

    code: int
    out: str
    err: str

    @property
    def text(self) -> str:
        return self.out + self.err


def run_engine(
    script: Path,
    *args: str,
    cwd: Path,
    state: Path | None = None,
    env_extra: dict[str, str] | None = None,
) -> Run:
    """Запустить скрипт движка в отдельном процессе.

    Путь к состоянию передаётся через `INSPECTION_FILE` — тот же механизм,
    которым бот будет разводить проверки по чатам. `env_extra` нужен тестам,
    которые имитируют машину без рабочего рендерера PDF.
    """
    env = dict(os.environ)
    if state is not None:
        env["INSPECTION_FILE"] = str(state)
    env.update(env_extra or {})
    p = subprocess.run(  # noqa: S603 — аргументы собираем сами, ввода извне тут нет
        [sys.executable, str(script), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )
    return Run(p.returncode, p.stdout, p.stderr)


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """Пустая рабочая папка проверки — аналог папки на чат."""
    d = tmp_path / "chat-1"
    d.mkdir()
    return d


@pytest.fixture
def audit(workdir: Path) -> Callable[..., Run]:
    """Вызов `audit.py` в рабочей папке с состоянием `inspection.json` в ней."""

    def call(*args: str) -> Run:
        return run_engine(AUDIT, *args, cwd=workdir, state=workdir / "inspection.json")

    return call


@pytest.fixture
def report(workdir: Path) -> Callable[..., Run]:
    """Вызов `report.py` в той же рабочей папке."""

    def call(*args: str, env_extra: dict[str, str] | None = None) -> Run:
        return run_engine(
            REPORT, *args, cwd=workdir, state=workdir / "inspection.json", env_extra=env_extra
        )

    return call


@pytest.fixture
def started(audit: Callable[..., Run]) -> Callable[..., Run]:
    """Начатая проверка: минимальная шапка, дальше можно добавлять записи."""
    r = audit("init", "--unit", "Тестовая", "--auditor", "Тест", "--date", "2026-08-21")
    assert r.code == 0, r.text
    return audit


@pytest.fixture
def no_renderer(tmp_path: Path) -> dict[str, str]:
    """Окружение, в котором ни один рендерер PDF не работает.

    Проверяем именно поведение при провале сборки: заглушка `wkhtmltopdf`
    падает, chromium в урезанном PATH не находится, а импорт `weasyprint`
    подменён модулем, который бросает исключение.
    """
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir()
    wk = stub_bin / "wkhtmltopdf"
    wk.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    wk.chmod(0o755)
    stub_lib = tmp_path / "stub-lib"
    stub_lib.mkdir()
    (stub_lib / "weasyprint.py").write_text(
        "raise ImportError('рендерер недоступен — заглушка теста')\n", encoding="utf-8"
    )
    return {"PATH": f"{stub_bin}:/usr/bin:/bin", "PYTHONPATH": str(stub_lib)}
