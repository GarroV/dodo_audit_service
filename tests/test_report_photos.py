"""T043: пропавшая фотография заметна, а не исчезает из отчёта молча.

Кадр в боте хранится идентификатором телеграма и скачивается во временный файл
только на сборке отчёта (`attach_photo` в блоке `domain`). Значит промах пути
случается там, где путь резолвится, — в этом блоке, а не в движке. Молчаливая
пустота на месте фотографии хуже отказа: партнёр видит нарушение без
доказательства и справедливо его оспаривает.

Проверяется два уровня:
* движок — при промахе рисует видимую отметку вместо пустоты (`report.py html`);
* блок — не отдаёт отчёт с потерянным кадром за готовый, а называет запись.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import Run

from src.domain import add_finding, attach_photo, start_inspection
from src.report import build_pdf
from src.report.errors import PhotoMissing

CHAT = 4002
ОТМЕТКА = "Фотография не приложена"

HAS_PDFTOTEXT = shutil.which("pdftotext") is not None
requires_pdftotext = pytest.mark.skipif(
    not HAS_PDFTOTEXT, reason="нет pdftotext (poppler) — текст из PDF не достать"
)


def кадр(path: Path) -> Path:
    """Настоящий JPEG: движок открывает кадр через Pillow и сжимает его."""
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (80, 60), (200, 120, 60)).save(path, "JPEG")
    return path


def текст_pdf(path: Path) -> str:
    out = subprocess.run(  # noqa: S603 — аргументы собраны здесь, оболочки нет
        [shutil.which("pdftotext") or "pdftotext", str(path), "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


@pytest.fixture
def проверка(domain_env: Path) -> Path:
    start_inspection(
        CHAT,
        unit="Белград-1",
        kind="planned",
        report_lang="ru",
        date="2026-08-21",
        auditor="Василий Гарро",
    )
    add_finding(CHAT, code="CLN05", level="D1", zone="hot_kitchen", text="Нагар на подине печи")
    return domain_env


# --- движок: отметка вместо пустоты ------------------------------------------


def test_движок_рисует_отметку_на_месте_пропавшего_кадра(
    started: Callable[..., Run], report: Callable[..., Run], workdir: Path
) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    started("photo", "1", "--add", "photos/нет-такого.jpg")
    r = report("html")
    assert r.code == 0, r.text
    assert ОТМЕТКА in r.out, "кадр пропал из отчёта молча — в разметке нет отметки"


def test_движок_сообщает_о_пропавшем_кадре_в_stderr(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    started("photo", "1", "--add", "photos/нет-такого.jpg")
    r = report("html")
    assert "нет-такого.jpg" in r.err, f"промах пути не назван в stderr: {r.err!r}"


def test_настоящий_кадр_попадает_в_отчёт_без_отметки(
    started: Callable[..., Run], report: Callable[..., Run], workdir: Path
) -> None:
    кадр(workdir / "photos" / "есть.jpg")
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    started("photo", "1", "--add", "photos/есть.jpg")
    r = report("html")
    assert r.code == 0, r.text
    assert "<img" in r.out, "настоящий кадр не попал в отчёт"
    assert ОТМЕТКА not in r.out, "на месте существующего кадра стоит отметка о пропаже"


def test_карта_кадров_резолвит_идентификатор_телеграма(
    started: Callable[..., Run], report: Callable[..., Run], workdir: Path, tmp_path: Path
) -> None:
    """Идентификатор телеграма путём не является: движок обязан ходить по карте."""
    файл = кадр(tmp_path / "скачано" / "AgACAgIAAxkBAAI.jpg")
    карта = tmp_path / "map.json"
    карта.write_text(f'{{"AgACAgIAAxkBAAI": "{файл}"}}', encoding="utf-8")
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    started("photo", "1", "--add", "AgACAgIAAxkBAAI")
    r = report("html", "--photo-map", str(карта))
    assert r.code == 0, r.text
    assert "<img" in r.out, "кадр из карты не попал в отчёт"
    assert ОТМЕТКА not in r.out, "кадр есть в карте, а в отчёте отметка о пропаже"


def test_кадра_нет_в_карте_значит_отметка(
    started: Callable[..., Run], report: Callable[..., Run], workdir: Path, tmp_path: Path
) -> None:
    """С картой ссылка резолвится только по ней: путём идентификатор не считается."""
    кадр(workdir / "AgACAgIAAxkBAAI")
    карта = tmp_path / "map.json"
    карта.write_text("{}", encoding="utf-8")
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    started("photo", "1", "--add", "AgACAgIAAxkBAAI")
    r = report("html", "--photo-map", str(карта))
    assert r.code == 0, r.text
    assert ОТМЕТКА in r.out, "ссылки нет в карте, а отчёт собрался без отметки"


# --- блок: отчёт с потерянным кадром не выдаётся за готовый -------------------


def test_build_pdf_отказывает_при_пропавшем_кадре(проверка: Path) -> None:
    attach_photo(CHAT, 1, "photos/нет-такого.jpg")
    with pytest.raises(PhotoMissing) as отказ:
        build_pdf(CHAT)
    assert отказ.value.misses == [(1, "photos/нет-такого.jpg")], (
        f"отказ не называет запись и кадр: {отказ.value.misses!r}"
    )
    assert "1" in str(отказ.value), "в тексте отказа нет номера записи"


def test_build_pdf_собирает_отчёт_с_отметкой_если_разрешили(проверка: Path) -> None:
    """Проверка на точке закончена, кадр потерян — аудитор вправе собрать как есть."""
    attach_photo(CHAT, 1, "photos/нет-такого.jpg")
    out = build_pdf(CHAT, allow_missing_photos=True)
    assert out.is_file() and out.read_bytes()[:5] == b"%PDF-"


@requires_pdftotext
def test_отметка_видна_в_собранном_pdf(проверка: Path) -> None:
    attach_photo(CHAT, 1, "photos/нет-такого.jpg")
    текст = текст_pdf(build_pdf(CHAT, allow_missing_photos=True))
    assert ОТМЕТКА in текст, f"в PDF на месте пропавшего кадра пусто: {текст[:400]!r}"


def test_существующий_кадр_собирается_без_отказа(проверка: Path) -> None:
    кадр(проверка / f"chat_{CHAT}" / "photos" / "есть.jpg")
    attach_photo(CHAT, 1, "photos/есть.jpg")
    out = build_pdf(CHAT)
    assert out.is_file(), "отчёт с настоящим кадром не собрался"


def test_кадр_скачивается_переданной_функцией(проверка: Path, tmp_path: Path) -> None:
    """В боте кадр берётся по file_id, а не с диска: резолвер передаётся снаружи."""
    файл = кадр(tmp_path / "скачано" / "кадр.jpg")
    спрошено: list[str] = []

    def скачать(file_id: str) -> Path:
        спрошено.append(file_id)
        return файл

    attach_photo(CHAT, 1, "AgACAgIAAxkBAAI")
    out = build_pdf(CHAT, fetch_photo=скачать)
    assert спрошено == ["AgACAgIAAxkBAAI"], f"резолвер спрошен не о том: {спрошено!r}"
    assert out.is_file()


def test_резолвер_вернул_пустоту_значит_кадр_потерян(проверка: Path) -> None:
    attach_photo(CHAT, 1, "AgACAgIAAxkBAAI")
    with pytest.raises(PhotoMissing):
        build_pdf(CHAT, fetch_photo=lambda _file_id: None)


def test_отчёт_без_фотографий_собирается_как_прежде(проверка: Path) -> None:
    out = build_pdf(CHAT)
    assert out.is_file(), "проверка без кадров перестала давать отчёт"
