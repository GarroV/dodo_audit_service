#!/usr/bin/env python3
"""Движок проверки пиццерии: чек-лист, фиксация нарушений, расчёт оценки.

Использование (все команды печатают результат в stdout):

  audit.py index [--zone CODE] [--process NAME] [--q ТЕКСТ]
        компактный список вопросов чек-листа (id, вопрос, доступные D, зоны, срок)
  audit.py detail PRD01,FSB05
        полные критерии D1/D2/D3 по указанным вопросам
  audit.py zones
        список физических зон с долями
  audit.py init --unit "..." [--city ...] [--partner ...] [--auditor ...] [--type ...] [--date ...] [--lang ru|en]
        создать новую проверку (inspection.json в текущей папке)
  audit.py meta [--unit ...] [--city ...] [--partner ...] [--auditor ...] [--date ...] [--lang ...]
        поправить шапку уже начатой проверки, не трогая зафиксированные записи
  audit.py add --qid PRD01 --level D2 --zone fridge [--photo путь] [--comment "..."] [--evidence "..."]
        зафиксировать нарушение (можно повторять одно и то же qid в разных зонах).
        --photo можно указать несколько раз или через запятую — все ракурсы одного
        нарушения идут в одну запись
  audit.py edit --n N [--qid PRD01] [--level D2] [--zone fridge] [--evidence "..."] [--comment "..."]
        поправить уже зафиксированное нарушение #N. Синонимы из контракта блока:
        --code = --qid, --text = --evidence. Меняются только переданные поля
  audit.py photo N --add путь1,путь2 [--clear]
        доснять фото к уже зафиксированному нарушению #N
  audit.py drop N            удалить нарушение по номеру
  audit.py info --qid INF01 --text "..."
        заполнить информационный пункт (сильные стороны, зоны роста, вид проверки и т.п.)
  audit.py list              показать зафиксированное
  audit.py score             посчитать процент, букву и разбивку (JSON)

Файлы данных лежат рядом со скриптом в ../data и правятся вручную:
  checklist.csv  — вопросы (одна строка = один вопрос, добавлять/удалять свободно)
  criteria.md    — критерии D1/D2/D3 по вопросам
  zones.csv      — физические зоны и их доли в 100%
  scoring.json   — ставки вычетов и матрица букв
"""
import argparse, csv, json, os, re, sys, tempfile, time
from contextlib import contextmanager
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, os.pardir, "data")
STATE = os.environ.get("INSPECTION_FILE", "inspection.json")

# Доли зон обязаны давать 100%: на них строится вся разбивка оценки.
ZONE_SHARE_TOTAL = 100.0
# Допуск на десятичное округление: равный дележ на 3, 7 или 11 зон не даёт
# ровно 100 (manage.py пишет 100/N с четырьмя знаками — остаток 0.0001).
# Половина сотой доли процента — уже не представление числа, а правка данных:
# сумма 99.99 из задачи T103 обязана быть замеченной, а не прощённой.
ZONE_SHARE_TOLERANCE = 0.005


def active_dir():
    """Где лежит рабочая копия чек-листа. Приоритет: $CHECKLIST_DIR, ./checklist_data, файлы плагина."""
    for d in (os.environ.get("CHECKLIST_DIR"), os.path.join(os.getcwd(), "checklist_data")):
        if d and os.path.isdir(d) and os.path.exists(os.path.join(d, "checklist.csv")):
            return d
    return os.path.normpath(DATA)


def data_path(name):
    d = active_dir()
    p = os.path.join(d, name)
    if os.path.exists(p):
        return p
    return os.path.normpath(os.path.join(DATA, name))


#: Строка методики, которую движок пропускает намеренно: `id` начинается с
#: решётки. Так в `checklist.csv` оставляют пометки — «набор иллюстративный»,
#: «дальше пошёл склад». Правило живёт в одной функции на весь движок: своя
#: трактовка у `manage.py validate` уже разошлась с этой и объявляла
#: комментарий сломанной строкой (T168).
COMMENT_PREFIX = "#"


def is_comment_row(qid):
    """Строка-комментарий чек-листа: `id` начинается с решётки."""
    return str(qid or "").strip().startswith(COMMENT_PREFIX)


def load_checklist():
    """Вопросы чек-листа. Дубль id и нечитаемый срок устранения — отказ, не подмена.

    Раньше дубль id пропускался с предупреждением в sys.stderr, которое никто
    не читает, — в отчёт молча попадал не тот вопрос (пункты связываются
    кодами, а не формулировками). А нечисловой срок устранения (days) молча
    становился 0, и report.py печатает при нулевом сроке «немедленно» —
    партнёр получал предписание устранить рядовое нарушение сегодня же.
    Молчаливой нормализации здесь быть не может (задача T106).

    Пустая клетка days остаётся нулём и отказа не вызывает: это осознанное
    решение — manage.py add без --days пишет в CSV именно пустоту, и запрет
    сломал бы штатное заведение пункта.
    """
    path = data_path("checklist.csv")
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    seen = {}
    out = []
    for n, r in enumerate(rows, start=2):
        qid = (r.get("id") or "").strip()
        if not qid or is_comment_row(qid):
            continue
        if qid in seen:
            sys.exit(
                f"Дубль id в чек-листе: «{qid}» встречается в строках {seen[qid]} и {n}. "
                f"Файл: {path}. Пункты связываются кодами, а не формулировками: две строки "
                f"с одним кодом означают, что в отчёт попадёт не тот вопрос. Оставьте одну "
                f"строку или смените код."
            )
        seen[qid] = n
        r["id"] = qid
        r["levels"] = [x for x in re.split(r"[;,]", r.get("levels", "")) if x.strip()]
        r["levels"] = [x.strip().upper() for x in r["levels"]]
        r["zones"] = [z.strip() for z in (r.get("zones") or "").split(",") if z.strip()]
        raw_days = (r.get("days") or "").strip()
        if not raw_days:
            r["days"] = 0
        else:
            try:
                r["days"] = int(float(raw_days))
            except (ValueError, TypeError):
                sys.exit(
                    f"Срок устранения не число: у пункта «{qid}» в колонке days стоит "
                    f"«{raw_days}». Файл: {path}, строка {n}. Поставьте целое число дней — "
                    f"иначе срок в предписании партнёру превращается в «немедленно», и "
                    f"рядовое нарушение выглядит критическим."
                )
        r["kind"] = (r.get("kind") or "violation").strip()
        out.append(r)
    return out


def load_zones():
    """Физические зоны и их доли. Сумма долей обязана сходиться к 100%.

    Раньше несходящаяся сумма молча переписывала ВСЕ доли на равные (100/N).
    Пока десять зон весят по 10%, подмена незаметна; на неравных долях
    (кухня тяжелее фасада) первое же округление превращало методику в другую,
    и отчёт уходил партнёру посчитанным не по той. Молчаливой нормализации
    здесь быть не может: расхождение — отказ с причиной (задача T103).
    """
    path = data_path("zones.csv")
    with open(path, encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("code") or "").strip()]
    for r in rows:
        code = r["code"]
        raw = (r.get("share_pct") or "").strip()
        if not raw:
            sys.exit(
                f"Доля зоны не заполнена: у зоны «{code}» пусто в колонке share_pct. "
                f"Файл: {path}. Доли зон складываются в 100% и задают вес зоны в оценке — "
                f"без числа зона молча теряет вес."
            )
        try:
            r["share_pct"] = float(raw)
        except ValueError:
            sys.exit(
                f"Доля зоны не число: у зоны «{code}» в колонке share_pct стоит «{raw}». "
                f"Файл: {path}. Доли зон складываются в 100% и задают вес зоны в оценке — "
                f"без числа зона молча теряет вес."
            )
    total = sum(r["share_pct"] for r in rows)
    if rows and abs(total - ZONE_SHARE_TOTAL) > ZONE_SHARE_TOLERANCE:
        sys.exit(
            f"Доли зон не сходятся: сумма share_pct = {total:g}%, "
            f"ожидается {ZONE_SHARE_TOTAL:g}%. Файл: {path}. "
            f"Поправьте колонку share_pct — считать оценку по такой методике нельзя."
        )
    return rows


def load_cfg():
    with open(data_path("scoring.json"), encoding="utf-8") as f:
        return json.load(f)


def zone_codes(cl_row, zones):
    if cl_row["zones"] == ["*"] or not cl_row["zones"]:
        return [z["code"] for z in zones]
    return cl_row["zones"]


def load_state():
    if not os.path.exists(STATE):
        sys.exit(f"Проверка не начата: файла {STATE} нет. Сначала audit.py init --unit \"...\"")
    with open(STATE, encoding="utf-8") as f:
        return json.load(f)


def state_dir():
    return os.path.dirname(os.path.abspath(STATE)) or "."


def save_state(st):
    """Атомарная запись: сначала временный файл рядом, потом os.replace.

    Решение D007 — состояние живёт в файле, значит альбом из нескольких кадров
    пишется параллельно. Запись поверх файла оставляла обрезанный JSON.
    """
    d = state_dir()
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".inspection-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _lock_exclusive(fh):
    """Взять исключительную блокировку без ожидания. False — занято."""
    try:
        import fcntl
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
    except ImportError:
        import msvcrt
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False


def _unlock(fh):
    try:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except ImportError:
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


LOCK_TIMEOUT_SEC = 30.0


@contextmanager
def state_lock():
    """Блокировка на проверку: один файл состояния — один пишущий процесс.

    Читающие команды блокировку не берут — им хватает атомарности замены файла.
    """
    d = state_dir()
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, os.path.basename(STATE) + ".lock")
    fh = open(path, "a+")
    try:
        deadline = time.time() + LOCK_TIMEOUT_SEC
        while not _lock_exclusive(fh):
            if time.time() > deadline:
                sys.exit(f"Состояние {STATE} занято другим процессом дольше "
                         f"{LOCK_TIMEOUT_SEC:g} с — запись отменена")
            time.sleep(0.01)
        try:
            yield
        finally:
            _unlock(fh)
    finally:
        fh.close()


def cmd_index(a):
    zones = {z["code"]: z for z in load_zones()}
    rows = load_checklist()
    for r in rows:
        if r["kind"] != "violation":
            continue
        if a.zone and a.zone not in zone_codes(r, list(zones.values())):
            continue
        if a.process and a.process.lower() not in r["process_ru"].lower():
            continue
        if a.q and a.q.lower() not in (r["question_ru"] + r["question_en"]).lower():
            continue
        zl = "все зоны" if (r["zones"] == ["*"] or not r["zones"]) else ",".join(r["zones"])
        print(f"{r['id']} | {r['process_ru']} | {r['question_ru']} | {'/'.join(r['levels'])} | {zl} | {r['days']}д")


def cmd_detail(a):
    ids = [x.strip().upper() for x in re.split(r"[,\s]+", a.ids) if x.strip()]
    text = open(data_path("criteria.md"), encoding="utf-8").read()
    blocks = dict(re.findall(r"\n## (\S+)\n(.*?)(?=\n## |\Z)", text, re.S))
    cl = {r["id"]: r for r in load_checklist()}
    for i in ids:
        r = cl.get(i)
        if not r:
            print(f"\n## {i}\nнет такого вопроса в чек-листе")
            continue
        zl = "все зоны" if (r["zones"] == ["*"] or not r["zones"]) else ",".join(r["zones"])
        print(f"\n## {i} [{'/'.join(r['levels'])}] зоны: {zl}, срок {r['days']}д")
        print(r["question_ru"])
        print(blocks.get(i, "(критерии не заданы)").strip())


def cmd_zones(a):
    for z in load_zones():
        print(f"{z['code']} | {z['name_ru']} | {z['name_en']} | {z['share_pct']:g}%")


LANGS = ("ru", "en")
META_FIELDS = ("unit", "city", "partner", "contact", "auditor", "type", "date", "lang")


def clean_unit(v):
    v = (v or "").strip()
    if not v:
        sys.exit('Не указана пиццерия: нужен --unit "Белград-1"')
    return v


def clean_date(v):
    v = (v or "").strip()
    try:
        return date.fromisoformat(v).isoformat()
    except (ValueError, TypeError):
        sys.exit(f"Дата «{v}» не в формате ISO. Нужен ГГГГ-ММ-ДД, например 2026-08-21")


def clean_lang(v):
    v = (v or "").strip().lower()
    if v not in LANGS:
        sys.exit(f"Язык «{v}» не поддерживается. Доступны: {', '.join(LANGS)}")
    return v


def inspection_date(st):
    """Дата проверки из шапки. От неё считается КАЖДЫЙ срок устранения.

    Раньше срок считался в `try/except Exception: pass`: нечитаемая дата
    оставляла `due` пустым по всем нарушениям, и молча. Дальше `report.py`
    подставлял вместо пропавшего срока «немедленно», а в письме срок плана
    действий превращался в `___` — партнёр получал предписание устранить
    рядовую D1 сегодня же. «Сроки устранения напечатаны в PDF» — объявленный
    пункт готовности MVP, поэтому здесь отказ, а не подстановка (задача T106).

    `init` и `meta` дату валидируют, но состояние — обычный JSON на диске:
    проверки, начатые до появления валидации шапки (T014), и файлы, поправленные
    руками, приходят сюда какими есть.
    """
    raw = (st.get("meta") or {}).get("date")
    try:
        return date.fromisoformat(str(raw))
    except (ValueError, TypeError):
        sys.exit(
            f"Дата проверки в шапке не читается: «{raw}». Нужен ISO-формат "
            f"ГГГГ-ММ-ДД, например 2026-08-21. Файл: {os.path.abspath(STATE)}. "
            f"От этой даты считаются все сроки устранения — без неё в предписании "
            f"партнёру каждое нарушение получит «немедленно». "
            f"Поправьте шапку: audit.py meta --date ГГГГ-ММ-ДД"
        )


def cmd_init(a):
    st = {"meta": {"unit": clean_unit(a.unit), "city": a.city or "", "partner": a.partner or "",
                   "contact": a.contact or "",
                   "auditor": a.auditor or "", "type": a.type or "Плановая",
                   "date": clean_date(a.date or date.today().isoformat()),
                   "lang": clean_lang(a.lang or "ru")},
          "findings": [], "info": {}, "seq": 0}
    save_state(st)
    print(json.dumps(st["meta"], ensure_ascii=False))


def cmd_meta(a):
    """Правка шапки уже начатой проверки: меняются только переданные поля."""
    st = load_state()
    given = {k: getattr(a, k) for k in META_FIELDS if getattr(a, k) is not None}
    if not given:
        sys.exit("Нечего менять: укажите хотя бы одно из "
                 + ", ".join(f"--{k}" for k in META_FIELDS))
    if "unit" in given:
        given["unit"] = clean_unit(given["unit"])
    if "date" in given:
        given["date"] = clean_date(given["date"])
    if "lang" in given:
        given["lang"] = clean_lang(given["lang"])
    st["meta"].update(given)
    save_state(st)
    print(json.dumps(st["meta"], ensure_ascii=False))


def split_photos(vals):
    """--photo можно указать несколько раз и/или через запятую."""
    out = []
    for v in (vals or []):
        for x in str(v).split(","):
            x = x.strip()
            if x and x not in out:
                out.append(x)
    return out


def photos_of(f):
    """Список фото у нарушения. Поддерживает и старое поле photo (одна строка)."""
    if f.get("photos"):
        return [x for x in f["photos"] if x]
    return [f["photo"]] if f.get("photo") else []


def cmd_add(a):
    cl = {r["id"]: r for r in load_checklist()}
    zones = load_zones()
    zc = {z["code"] for z in zones}
    qid = a.qid.strip().upper()
    if qid not in cl:
        sys.exit(f"Нет вопроса {qid} в чек-листе")
    r = cl[qid]
    lvl = a.level.strip().upper()
    if lvl not in r["levels"]:
        sys.exit(f"У вопроса {qid} нет уровня {lvl}. Доступны: {'/'.join(r['levels'])}")
    if a.zone not in zc:
        sys.exit(f"Нет зоны {a.zone}. Доступны: {', '.join(sorted(zc))}")
    allowed = zone_codes(r, zones)
    st = load_state()
    check_pair_free(st, qid, a.zone)
    # Счётчик сквозной: номера аудитор называет вслух, переиспользовать нельзя.
    n = max([f["n"] for f in st["findings"]] + [int(st.get("seq") or 0)]) + 1
    st["seq"] = n
    f = {"n": n, "qid": qid, "level": lvl, "zone": a.zone, "photos": split_photos(a.photo),
         "comment": a.comment or "", "evidence": a.evidence or ""}
    if a.zone not in allowed:
        f["zone_unusual"] = True
    st["findings"].append(f)
    save_state(st)
    warn = "  (зона нетипична для этого вопроса — перепроверьте)" if a.zone not in allowed else ""
    ph = f"  [фото: {len(f['photos'])}]" if f["photos"] else ""
    print(f"#{n} {qid} {lvl} / {a.zone}: {r['question_ru'][:90]}{ph}{warn}")


def find_by_n(st, n):
    for f in st["findings"]:
        if f["n"] == n:
            return f
    return None


def known_numbers(st):
    return ", ".join(f"#{f['n']}" for f in st["findings"]) or "ни одной записи"


def check_pair_free(st, qid, zone, skip_n=None):
    """Пара «пункт + зона» уникальна: один и тот же пункт в одной зоне — одно нарушение."""
    for f in st["findings"]:
        if f["n"] != skip_n and f["qid"] == qid and f["zone"] == zone:
            sys.exit(f"{qid} в зоне {zone} уже зафиксировано — запись #{f['n']}. "
                     f"Доснимите фото (audit.py photo {f['n']} --add ...) "
                     f"или поправьте её (audit.py edit --n {f['n']} ...)")


def cmd_edit(a):
    st = load_state()
    f = find_by_n(st, a.n)
    if f is None:
        sys.exit(f"Нарушения #{a.n} нет. Есть: {known_numbers(st)}")
    changed = [k for k, v in (("qid", a.qid), ("level", a.level), ("zone", a.zone),
                              ("evidence", a.evidence), ("comment", a.comment)) if v is not None]
    if not changed:
        sys.exit("Нечего менять: укажите хотя бы одно из "
                 "--qid/--level/--zone/--evidence/--comment")
    cl = {r["id"]: r for r in load_checklist()}
    zones = load_zones()
    zc = {z["code"] for z in zones}
    qid = (a.qid if a.qid is not None else f["qid"]).strip().upper()
    lvl = (a.level if a.level is not None else f["level"]).strip().upper()
    zone = (a.zone if a.zone is not None else f["zone"]).strip()
    if qid not in cl:
        sys.exit(f"Нет вопроса {qid} в чек-листе")
    r = cl[qid]
    if lvl not in r["levels"]:
        sys.exit(f"У вопроса {qid} нет уровня {lvl}. Доступны: {'/'.join(r['levels'])}")
    if zone not in zc:
        sys.exit(f"Нет зоны {zone}. Доступны: {', '.join(sorted(zc))}")
    check_pair_free(st, qid, zone, skip_n=f["n"])
    f["qid"], f["level"], f["zone"] = qid, lvl, zone
    if a.evidence is not None:
        f["evidence"] = a.evidence
    if a.comment is not None:
        f["comment"] = a.comment
    if zone in zone_codes(r, zones):
        f.pop("zone_unusual", None)
    else:
        f["zone_unusual"] = True
    save_state(st)
    warn = "  (зона нетипична для этого вопроса — перепроверьте)" if f.get("zone_unusual") else ""
    print(f"#{f['n']} {qid} {lvl} / {zone}: {r['question_ru'][:90]}{warn}")


def cmd_photo(a):
    st = load_state()
    hit = [f for f in st["findings"] if f["n"] == a.n]
    if not hit:
        sys.exit(f"Нарушения #{a.n} нет")
    f = hit[0]
    cur = photos_of(f)
    if a.clear:
        cur = []
    # Существование файла здесь НЕ проверяется намеренно: в боте фотография
    # хранится идентификатором телеграма и скачивается только на сборке отчёта.
    # Пропавший кадр ловится там, где путь резолвится, — задача T043 блока report.
    cur = cur + [x for x in split_photos(a.add) if x not in cur]
    f.pop("photo", None)
    f["photos"] = cur
    save_state(st)
    print(f"#{a.n}: фото — {len(cur)}")


def cmd_drop(a):
    st = load_state()
    before = len(st["findings"])
    st["findings"] = [f for f in st["findings"] if f["n"] != a.n]
    # Удалять нечего — это отказ, а не успех: бот по коду возврата скажет
    # аудитору «удалено», и тот пойдёт дальше с записью, которая осталась.
    if len(st["findings"]) == before:
        sys.exit(f"Нарушения #{a.n} нет")
    save_state(st)
    print("удалено")


def cmd_info(a):
    st = load_state()
    st.setdefault("info", {})[a.qid.strip().upper()] = a.text
    save_state(st)
    print("ок")


def cmd_list(a):
    st = load_state()
    cl = {r["id"]: r for r in load_checklist()}
    zn = {z["code"]: z["name_ru"] for z in load_zones()}
    print(json.dumps(st["meta"], ensure_ascii=False))
    for f in st["findings"]:
        q = cl.get(f["qid"], {}).get("question_ru", "?")
        n_ph = len(photos_of(f))
        ph = f" 📷×{n_ph}" if n_ph else ""
        print(f"#{f['n']} {f['level']} | {zn.get(f['zone'], f['zone'])} | {f['qid']} {q[:80]}{ph}")
    for k, v in (st.get("info") or {}).items():
        print(f"[инфо] {k}: {v[:120]}")


def compute(st, cl_rows, zones, cfg):
    inspected = inspection_date(st)
    cl = {r["id"]: r for r in cl_rows}
    zmap = {z["code"]: z for z in zones}
    pen = cfg["penalty"]
    d3cfg = cfg["d3"]
    d3_zones = {f["zone"] for f in st["findings"] if f["level"] == "D3"}
    counts = {"D1": 0, "D2": 0, "D3": 0}
    per_zone = {z["code"]: {"name_ru": z["name_ru"], "name_en": z["name_en"],
                            "share": z["share_pct"], "D1": 0, "D2": 0, "D3": 0,
                            "loss": 0.0, "zeroed": False} for z in zones}
    items = []
    for f in st["findings"]:
        r = cl.get(f["qid"], {})
        lvl = f["level"]
        counts[lvl] = counts.get(lvl, 0) + 1
        z = per_zone.setdefault(f["zone"], {"name_ru": f["zone"], "name_en": f["zone"],
                                            "share": 0.0, "D1": 0, "D2": 0, "D3": 0,
                                            "loss": 0.0, "zeroed": False})
        z[lvl] = z.get(lvl, 0) + 1
        counted = True
        cost = 0.0
        if lvl == "D3":
            z["zeroed"] = True
        else:
            if d3cfg.get("skip_other_violations_in_d3_zone", True) and f["zone"] in d3_zones:
                counted = False
            else:
                cost = float(pen.get(lvl, 0))
        days = min(r.get("days", 0), cfg["deadlines"]["max_days"].get(lvl, 99))
        due = (inspected + timedelta(days=days)).isoformat()
        items.append({**f, "photos": photos_of(f), "question_ru": r.get("question_ru", ""), "question_en": r.get("question_en", ""),
                      "process_ru": r.get("process_ru", ""), "process_en": r.get("process_en", ""),
                      "zone_name_ru": zmap.get(f["zone"], {}).get("name_ru", f["zone"]),
                      "zone_name_en": zmap.get(f["zone"], {}).get("name_en", f["zone"]),
                      "days": days, "due": due, "counted": counted, "cost": cost})

    deductions = 0.0
    for code, z in per_zone.items():
        loss = 0.0
        if z["zeroed"] and d3cfg.get("mode") == "zero_zone_share":
            loss = z["share"]
        else:
            loss = sum(i["cost"] for i in items if i["zone"] == code)
            if cfg.get("cap_zone_loss_at_share") and z["share"]:
                loss = min(loss, z["share"])
        z["loss"] = round(loss, 4)
        z["score"] = round(max(z["share"] - loss, 0.0), 4)
        deductions += loss

    if counts["D3"] and d3cfg.get("mode") == "zero_total":
        pct = 0.0
    else:
        pct = cfg["start_pct"] - deductions
    pct = max(pct, cfg.get("floor_pct", 0.0))
    pct = round(pct + 1e-9, 2)

    grade = None
    for rule in cfg["grades"]["rules"]:
        c = rule["if"]
        ok = True
        if "min_d3" in c and counts["D3"] < c["min_d3"]:
            ok = False
        if "min_d2" in c and counts["D2"] < c["min_d2"]:
            ok = False
        if "max_d2" in c and counts["D2"] > c["max_d2"]:
            ok = False
        if "pct_below" in c and not pct < c["pct_below"]:
            ok = False
        if "pct_at_least" in c and not pct >= c["pct_at_least"]:
            ok = False
        if ok:
            grade = rule
            break
    if grade is None:
        grade = cfg["grades"]["fallback"]

    by_process = {}
    for i in items:
        p = by_process.setdefault(i["process_ru"] or "—", {"D1": 0, "D2": 0, "D3": 0})
        p[i["level"]] = p.get(i["level"], 0) + 1

    return {"meta": st["meta"], "pct": pct, "grade": grade["grade"],
            "grade_label_ru": grade.get("label_ru", ""), "grade_label_en": grade.get("label_en", ""),
            "counts": counts, "deductions": round(deductions, 2),
            "zones": per_zone, "by_process": by_process, "findings": items,
            "info": st.get("info", {})}


def cmd_score(a):
    res = compute(load_state(), load_checklist(), load_zones(), load_cfg())
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return
    c = res["counts"]
    print(f"Итог: {res['pct']:g}%  оценка {res['grade']} — {res['grade_label_ru']}")
    print(f"Нарушений: D1 = {c['D1']}, D2 = {c['D2']}, D3 = {c['D3']}. Вычтено {res['deductions']:g} п.п.")
    for code, z in res["zones"].items():
        if z["D1"] or z["D2"] or z["D3"]:
            tag = "  ← обнулена (D3)" if z["zeroed"] else ""
            print(f"  {z['name_ru']}: D1 {z['D1']}, D2 {z['D2']}, D3 {z['D3']}, потеряно {z['loss']:g} из {z['share']:g}%{tag}")


# Команды, которые меняют состояние: они и только они берут блокировку.
MUTATING = {"init", "meta", "add", "edit", "photo", "drop", "info"}


def main():
    p = argparse.ArgumentParser(add_help=True)
    s = p.add_subparsers(dest="cmd", required=True)

    i = s.add_parser("index"); i.add_argument("--zone"); i.add_argument("--process"); i.add_argument("--q"); i.set_defaults(fn=cmd_index)
    d = s.add_parser("detail"); d.add_argument("ids"); d.set_defaults(fn=cmd_detail)
    z = s.add_parser("zones"); z.set_defaults(fn=cmd_zones)
    n = s.add_parser("init")
    for f in ("unit", "city", "partner", "contact", "auditor", "type", "date", "lang"):
        n.add_argument(f"--{f}")
    n.set_defaults(fn=cmd_init)
    mt = s.add_parser("meta")
    for f in META_FIELDS:
        mt.add_argument(f"--{f}")
    mt.set_defaults(fn=cmd_meta)
    ad = s.add_parser("add")
    ad.add_argument("--qid", required=True); ad.add_argument("--level", required=True)
    ad.add_argument("--zone", required=True)
    ad.add_argument("--photo", action="append",
                    help="путь к фото; можно указать несколько раз или через запятую")
    ad.add_argument("--comment"); ad.add_argument("--evidence")
    ad.set_defaults(fn=cmd_add)
    ed = s.add_parser("edit")
    ed.add_argument("--n", type=int, required=True)
    ed.add_argument("--qid", "--code", dest="qid")
    ed.add_argument("--level"); ed.add_argument("--zone")
    ed.add_argument("--evidence", "--text", dest="evidence")
    ed.add_argument("--comment")
    ed.set_defaults(fn=cmd_edit)
    ph = s.add_parser("photo"); ph.add_argument("n", type=int)
    ph.add_argument("--add", action="append"); ph.add_argument("--clear", action="store_true")
    ph.set_defaults(fn=cmd_photo)
    dr = s.add_parser("drop"); dr.add_argument("n", type=int); dr.set_defaults(fn=cmd_drop)
    inf = s.add_parser("info"); inf.add_argument("--qid", required=True); inf.add_argument("--text", required=True); inf.set_defaults(fn=cmd_info)
    ls = s.add_parser("list"); ls.set_defaults(fn=cmd_list)
    sc = s.add_parser("score"); sc.add_argument("--json", action="store_true"); sc.set_defaults(fn=cmd_score)

    a = p.parse_args()
    if a.cmd in MUTATING:
        with state_lock():
            a.fn(a)
    else:
        a.fn(a)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        pass
