"""T040: сборка PDF и письма через блок `report`.

Отчёт уходит партнёру, поэтому проверяется не «вызов не упал», а наблюдаемый
результат: файл существует, начинается с `%PDF-` и назван так же, как прежние
отчёты в `examples/`. Провал сборки обязан быть исключением — путь к
несуществующему файлу бот отдал бы аудитору как готовый отчёт.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.domain import add_finding, start_inspection
from src.domain.errors import InspectionNotStarted
from src.report import build, build_letter, build_pdf
from src.report.errors import PdfNotBuilt, ReportError

CHAT = 4001


@pytest.fixture
def проверка(domain_env: Path) -> Path:
    """Начатая проверка с одной записью. Возвращает каталог состояния."""
    start_inspection(
        CHAT,
        unit="Белград-1",
        kind="Плановая",
        report_lang="ru",
        date="2026-08-21",
        auditor="Василий Гарро",
        city="Белград",
    )
    add_finding(CHAT, code="CLN05", level="D1", zone="hot_kitchen", text="Нагар на подине печи")
    return domain_env


def test_pdf_собран_и_это_настоящий_pdf(проверка: Path) -> None:
    out = build_pdf(CHAT)
    assert out.is_file(), f"вернулся путь к несуществующему файлу: {out}"
    assert out.read_bytes()[:5] == b"%PDF-", "собрано что-то, но это не PDF"
    assert out.stat().st_size > 1000, "PDF подозрительно пустой"


def test_имя_файла_прежнее(проверка: Path) -> None:
    """Имя отчёта — часть договорённости с партнёром, менять его нельзя."""
    out = build_pdf(CHAT)
    assert out.name == "Аудит Белград-1 - Василий Гарро - 21.08.2026.pdf", (
        f"имя файла разошлось с прежними отчётами: {out.name!r}"
    )


def test_провал_сборки_поднимает_исключение(
    проверка: Path, monkeypatch: pytest.MonkeyPatch, no_renderer: dict[str, str], tmp_path: Path
) -> None:
    """Без рендерера отчёта нет — и вызов обязан сказать об этом, а не вернуть путь."""
    for name, value in no_renderer.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(PdfNotBuilt) as отказ:
        build_pdf(CHAT)
    assert "PDF" in str(отказ.value) or "отчёт" in str(отказ.value).lower()


def test_провал_сборки_не_выдаёт_за_отчёт_прошлый_файл(
    проверка: Path, monkeypatch: pytest.MonkeyPatch, no_renderer: dict[str, str]
) -> None:
    """Бот пишет отчёт в одно и то же место: прошлый файл не доказательство сборки."""
    for name, value in no_renderer.items():
        monkeypatch.setenv(name, value)
    прошлый = проверка / f"chat_{CHAT}" / "Аудит Белград-1 - Василий Гарро - 21.08.2026.pdf"
    прошлый.write_bytes(b"%PDF-1.4 old report" + b"\0" * 3000)
    with pytest.raises(PdfNotBuilt):
        build_pdf(CHAT)
    assert прошлый.exists(), "провал новой сборки снёс прошлый отчёт"


def test_pdf_без_начатой_проверки_отказ(domain_env: Path) -> None:
    with pytest.raises(InspectionNotStarted):
        build_pdf(777001)


def test_письмо_собирается(проверка: Path) -> None:
    текст = build_letter(CHAT)
    assert len(текст.strip()) > 200, f"письмо подозрительно короткое: {текст!r}"
    assert "Белград-1" in текст, "в письме нет пиццерии"


def test_письмо_чистой_проверки_не_требует_плана(проверка: Path) -> None:
    assert "просим до" not in build_letter(CHAT).lower(), (
        "с проверки без D2 и D3 требуют план действий"
    )


def test_письмо_с_нарушением_d2_требует_план(проверка: Path) -> None:
    add_finding(CHAT, code="PRD09", level="D2", zone="fridge", text="Тара без маркировки")
    текст = build_letter(CHAT).lower()
    assert "просим до" in текст and "план действий" in текст, (
        "при D2 письмо обязано требовать план действий"
    )


def test_шаблон_письма_меняется_от_наличия_d2(проверка: Path) -> None:
    чистое = build_letter(CHAT)
    add_finding(CHAT, code="PRD09", level="D2", zone="fridge", text="Тара без маркировки")
    с_нарушением = build_letter(CHAT)
    assert чистое != с_нарушением, "письмо не зависит от того, есть ли существенные нарушения"


def test_письмо_без_начатой_проверки_отказ(domain_env: Path) -> None:
    with pytest.raises(InspectionNotStarted):
        build_letter(777002)


# --- «успех» без работы: то, из-за чего блок и переписан ----------------------


def test_успех_движка_без_файла_это_провал(проверка: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Движок назвал файл и вернул ноль, а файла нет — это провал, а не отчёт."""
    monkeypatch.setattr(
        build, "run_report", lambda *_a, **_k: "Аудит Белград-1 - нет такого файла.pdf\n"
    )
    with pytest.raises(PdfNotBuilt):
        build_pdf(CHAT)


def test_не_pdf_под_именем_отчёта_это_провал(
    проверка: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Собрался файл, но это не PDF: партнёр получил бы нечитаемое вложение."""
    имя = "Аудит Белград-1 - Василий Гарро - 21.08.2026.pdf"

    def подделка(*_a: object, **_k: object) -> str:
        (проверка / f"chat_{CHAT}" / имя).write_bytes(b"<html>not a pdf</html>" * 200)
        return имя

    monkeypatch.setattr(build, "run_report", подделка)
    with pytest.raises(PdfNotBuilt):
        build_pdf(CHAT)


def test_пустое_письмо_это_провал(проверка: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build, "run_report", lambda *_a, **_k: "   \n")
    with pytest.raises(ReportError):
        build_letter(CHAT)
