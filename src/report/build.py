"""Сборка отчёта и письма партнёру.

Контракт блока — `docs/forge/blocks/report.md`. Наружу торчат две функции, и обе
проверяют наблюдаемый результат, а не отсутствие исключения: `build_pdf`
убеждается, что файл есть и это PDF, `build_letter` — что письмо не пустое.
Ни одна операция не возвращает код успеха, не сделав работу (конституция).

Разметка отчёта, выбор шаблона письма и расчёт оценки живут в движке. Здесь их
нет и быть не может: вторая копия правил разойдётся с первой, и увидит это
только партнёр.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from src.domain.engine import chat_dir

from .engine import run_report, settings
from .errors import PdfNotBuilt, PhotoMissing, ReportError
from .photos import FetchPhoto, misses_text, resolve_photos

#: Меньше этого не бывает даже у пустого отчёта — значит, собралась заглушка.
PDF_MIN_BYTES = 1000


def _pdf_problem(path: Path) -> str | None:
    """Почему собранное не является отчётом. `None` — отчёт на месте."""
    if not path.is_file():
        return f"файла нет: {path}"
    size = path.stat().st_size
    if size < PDF_MIN_BYTES:
        return f"файл слишком мал ({size} байт)"
    with path.open("rb") as fh:
        if fh.read(5) != b"%PDF-":
            return "собран файл, но это не PDF"
    return None


def build_pdf(
    chat_id: int,
    *,
    fetch_photo: FetchPhoto | None = None,
    allow_missing_photos: bool = False,
) -> Path:
    """Собрать отчёт по проверке этого чата и вернуть путь к файлу.

    Имя файла даёт движок — `Аудит <пиццерия> - <аудитор> - <дд.мм.гггг>.pdf`;
    здесь оно не повторяется, иначе две копии правила разъедутся при первой же
    правке. Отчёт кладётся в папку проверки, рядом с состоянием.

    Провал сборки — `PdfNotBuilt`, а не путь к тому, чего нет.

    `fetch_photo` — как получить файл кадра по идентификатору телеграма; без
    него ссылка читается как путь. Потерянный кадр по умолчанию отменяет
    сборку (`PhotoMissing` с номерами записей): отчёт без доказательства
    партнёр справедливо оспорит. `allow_missing_photos` оставляет решение
    аудитору — он уже уехал с точки и переснять не может; тогда на месте кадра
    печатается видимая отметка, а не пустота.
    """
    env = settings()
    plan = resolve_photos(chat_id, env, fetch_photo)
    if plan.misses and not allow_missing_photos:
        raise PhotoMissing(misses_text(plan.misses), misses=plan.misses)
    # Карта уходит движку всегда, даже пустая: ссылка резолвится в одном месте,
    # и идентификатор телеграма ни при каких условиях не уедет в него путём.
    with tempfile.TemporaryDirectory(prefix="report-photos-") as tmp:
        photo_map = Path(tmp) / "photo-map.json"
        photo_map.write_text(json.dumps(plan.mapping, ensure_ascii=False), encoding="utf-8")
        try:
            printed = run_report(
                ["pdf", "--photo-map", str(photo_map)], chat_id=chat_id, settings=env
            ).strip()
        except PdfNotBuilt:
            raise
        except ReportError as exc:
            raise PdfNotBuilt(f"Отчёт не собран: {exc}") from exc
    if not printed:
        raise PdfNotBuilt("Сборщик отчёта отчитался об успехе, но не назвал файл")
    out = Path(printed)
    if not out.is_absolute():
        out = chat_dir(chat_id, env) / out
    bad = _pdf_problem(out)
    if bad:
        raise PdfNotBuilt(f"Сборщик отчёта отчитался об успехе, но отчёта нет: {bad}")
    return out


def build_letter(chat_id: int) -> str:
    """Собрать текст письма партнёру.

    Шаблон выбирает движок по наличию D2 и D3: чистая проверка не требует плана
    действий по нарушениям, которых нет. Третьего шаблона здесь не заводится.
    """
    env = settings()
    text = run_report(["letter"], chat_id=chat_id, settings=env)
    if not text.strip():
        raise ReportError("Сборщик письма вернул пустой текст — отправлять партнёру нечего")
    return text.strip()
