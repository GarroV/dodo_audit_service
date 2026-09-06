"""Идентификатор версии методики: имя набора, дата публикации, отпечаток данных.

Решение D050: любая правка чек-листа — новая версия с датой, без исключений.
Идентификатор составной (`imf-2026-09-01-3f5a91b2c7d0`), и каждая часть закрывает
свой промах:

* **имя и дата** нужны человеку — по ним отчёт годичной давности опознаётся без
  базы и без кода;
* **отпечаток данных** нужен машине — он не даёт выпустить изменённую методику
  под прежним именем, если дату поднять забыли. Ровно так проверки, посчитанные
  по разной методике, и становились неотличимы.

Имя и дату ставит управляющая компания файлом `checklist_version.txt` в каталоге
методики. Файла нет — набор никто не издавал, и остаётся один отпечаток
(`local-…`): выдумывать за УК имя и дату здесь нельзя, иначе в отчёте появится
издание, которого не было.

Модуль работает с путём, а не с `Settings`: его зовут и `config`
(проверка окружения на старте), и `checklist` (сборка идентификатора), а
обратный импорт `config` замкнул бы их друг на друга.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from .errors import ConfigError, DomainError

#: Имя набора и дата публикации от управляющей компании. Файл необязательный.
VERSION_FILE = "checklist_version.txt"

#: Сколько знаков отпечатка попадает в идентификатор. Двенадцать, а не шесть из
#: примера D050: длина в решении иллюстративная, а хвост подлиннее стоит ноль и
#: снимает вопрос о совпадении отпечатков разных изданий.
FINGERPRINT_LEN = 12

#: Имя набора, когда издания не было. Отдельное слово, а не пустое место: в
#: отчёте должно быть видно, что имя и дату никто не проставлял.
LOCAL_NAME = "local"

#: «Имя набора плюс дата»: дата в конце строки, до неё — имя. Разделители между
#: ними любые (`imf 2026-09-01`, `imf-2026-09-01`, `imf, 2026-09-01`).
PUBLISHED = re.compile(r"^(?P<name>.+?)[\s\-_,.]*(?P<day>\d{4}-\d{2}-\d{2})$")

#: Пример правильной формы. Живёт одной строкой, потому что попадает в каждый
#: отказ: человек, которому отказали, должен увидеть, как надо, а не догадываться.
EXAMPLE = "imf 2026-09-01"


def _statement(path: Path) -> str | None:
    """Первая содержательная строка файла версии. Пустые и `#` — комментарии."""
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text and not text.startswith("#"):
            return text
    return None


def _refuse(path: Path, why: str) -> ConfigError:
    return ConfigError(
        f"Файл версии методики {path} ({VERSION_FILE}) не читается как «имя набора плюс дата»: "
        f"{why}. Нужна строка вида «{EXAMPLE}» — имя набора и дата публикации в конце. "
        f"Без даты правка методики теряется: решение D050"
    )


def published(data_dir: Path) -> tuple[str, str] | None:
    """Имя набора и дата публикации из `checklist_version.txt`.

    Файла нет — `None`: это законный случай, набор просто не издавали. Файл есть,
    но не разбирается — отказ: молча превратить испорченное издание в `local-…`
    означало бы выдать чужую методику за неизданную и потерять дату навсегда.
    """
    path = data_dir / VERSION_FILE
    if not path.is_file():
        return None
    statement = _statement(path)
    if statement is None:
        raise _refuse(path, "файл пустой")
    hit = PUBLISHED.match(statement)
    if hit is None:
        raise _refuse(path, f"в строке «{statement}» нет даты вида ГГГГ-ММ-ДД в конце")
    day = hit.group("day")
    try:
        date.fromisoformat(day)
    except ValueError:
        raise _refuse(path, f"даты {day} нет в календаре") from None
    name = re.sub(r"\s+", "-", hit.group("name").strip()).strip("-_,.")
    if not name:
        raise _refuse(path, f"в строке «{statement}» есть дата, но нет имени набора")
    return name, day


def fingerprint(data_dir: Path, names: Sequence[str]) -> str:
    """Отпечаток содержимого методики. Отсутствующий необязательный файл пропускается.

    В хеш идёт и имя файла, а не только его содержимое: иначе перенос строки из
    одного файла методики в другой не менял бы отпечаток.
    """
    digest = hashlib.sha256()
    for name in names:
        path = data_dir / name
        if not path.is_file():
            continue
        digest.update(f"{name}\0".encode())
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:FINGERPRINT_LEN]


def compose(data_dir: Path, names: Sequence[str]) -> str:
    """Идентификатор версии целиком: `<имя>-<дата>-<отпечаток>` или `local-<отпечаток>`."""
    mark = fingerprint(data_dir, names)
    issue = published(data_dir)
    if issue is None:
        return f"{LOCAL_NAME}-{mark}"
    name, day = issue
    return f"{name}-{day}-{mark}"


def edition_of(data_dir: Path, names: Sequence[str]) -> str | None:
    """Издание, которым ЭТОТ каталог является на самом деле. Не издание — `None`.

    Вопрос «тот ли это каталог издания X» задают в двух местах и по разным
    поводам: полка снимков домена ищет издание идущей проверки
    (`domain.edition`), хранилище версий MCP — издание записанной
    (`mcp.letters.pinned`). Ответ на него обязан быть один, поэтому он живёт
    здесь, рядом с формулой, которой издание штампуется в проверку.

    Свести пришлось на дефекте (T236): полка домена отпечаток сверяла, а
    хранилище MCP довольствовалось именем каталога — и каталог с правильным
    именем и чужим содержимым подписывал бы ответы аудитора формулировками
    другой методики молча. Имя каталога доказательством не является: каталоги
    кладёт не один и тот же код.

    `None`, а не отказ, и на все три беды сразу — каталога нет, файлы не
    читаются, `checklist_version.txt` испорчен: каждая из них означает ровно
    «этим изданием каталог не является», а что делать дальше, решает
    спрашивающий. Полнота каталога отдельно не проверяется намеренно —
    пропавший файл методики в отпечаток не входит (`fingerprint` его
    пропускает), и отпечаток неполного каталога с изданием не сойдётся.
    """
    if not data_dir.is_dir():
        return None
    try:
        return compose(data_dir, names)
    except (DomainError, OSError):
        return None
