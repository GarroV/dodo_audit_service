#!/usr/bin/env python3
"""Собирает итоговый PDF-отчёт и черновик письма партнёру из inspection.json.

  report.py pdf  [--out отчёт.pdf | --out-dir КАТАЛОГ] [--lang ru|en] [--photos all|d2d3|none]
        без --out отчёт уходит в reports/ рядом с состоянием проверки
  report.py letter [--lang ru|en]      печатает текст письма в stdout
  report.py html [--out отчёт.html] [--lang ru|en] [--photos ...]
        тот же HTML, из которого собирается PDF — для сверки вида и тестов

Рендерер зафиксирован: WeasyPrint (решение D009). Шрифт с кириллицей лежит
в проекте (engine/assets/fonts) и подключается явным путём, чтобы вид отчёта
не зависел от того, что установлено на машине.
"""
import argparse, base64, json, mimetypes, os, re, sys, tempfile
from datetime import date, timedelta
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from audit import (  # noqa: E402
    compute, info_field, inspection_date, kind_title, load_cfg, load_checklist, load_state,
    load_zones, state_dir,
)

# Каталог вывода по умолчанию — рядом с состоянием проверки, но не в соседи
# ему: там же лежат эталонные отчёты (examples/), и сборка их затирала (T104).
REPORT_DIR_NAME = "reports"

T = {
    "ru": {
        "title": "Отчёт о проверке пиццерии", "unit": "Пиццерия", "city": "Город",
        "partner": "Партнёр", "auditor": "Аудитор", "type": "Вид проверки", "date": "Дата проверки",
        "result": "Результат проверки", "grade": "Оценка", "score": "Итоговый балл",
        "summary": "Сводка", "crit": "Критичность", "count": "Количество", "cost": "Вычет",
        "zones": "Разбивка по зонам", "zone": "Зона", "share": "Доля", "lost": "Потеряно",
        "left": "Осталось", "findings": "Зафиксированные нарушения", "no_findings":
        "Нарушений не зафиксировано.", "deadline": "Устранить до", "immediately": "немедленно",
        "comment": "Комментарий", "process": "Процесс", "info": "Дополнительно",
        "appendix": "Приложение. Информационные записи",
        "appendix_note": "Раздел носит справочный характер: перечисленные ниже записи "
                         "не являются нарушениями и не влияют на оценку.",
        "recorded": "Зафиксировано", "item": "Пункт стандарта",
        "method": "Методика расчёта", "zeroed": "обнулена критическим нарушением D3",
        "not_counted": "учтено в обнулении зоны", "page": "стр.",
        "method_text": ("Старт — 100%. Каждое нарушение D1 снижает результат на {d1} п.п., "
                        "каждое D2 — на {d2} п.п. Нарушение D3 полностью сжигает долю той зоны, "
                        "в которой оно зафиксировано. Вопросы, по которым нарушений не зафиксировано, "
                        "считаются выполненными. Буква: A — от 95% без D2 и D3; B — от 90% без D3 "
                        "и не более одного D2; C — два и более D2 либо результат ниже 90%; "
                        "D — хотя бы одно нарушение D3."),
        "photo_app": "Фотоприложение",
        "photo_missing": "Фотография не приложена",
        "no_photo": "Без фотофиксации",
    },
    "en": {
        "title": "Pizzeria inspection report", "unit": "Store", "city": "City",
        "partner": "Partner", "auditor": "Auditor", "type": "Inspection type", "date": "Inspection date",
        "result": "Result", "grade": "Grade", "score": "Final score",
        "summary": "Summary", "crit": "Severity", "count": "Count", "cost": "Deduction",
        "zones": "Breakdown by zone", "zone": "Zone", "share": "Share", "lost": "Lost",
        "left": "Remaining", "findings": "Recorded violations", "no_findings":
        "No violations recorded.", "deadline": "Fix by", "immediately": "immediately",
        "comment": "Comment", "process": "Process", "info": "Additional information",
        "appendix": "Appendix. Informational records",
        "appendix_note": "This section is for reference only: the records below are not "
                         "violations and do not affect the score.",
        "recorded": "Recorded", "item": "Standard item",
        "method": "Scoring method", "zeroed": "zeroed by a critical D3 violation",
        "not_counted": "covered by the zone reset", "page": "p.",
        "method_text": ("Starting score is 100%. Each D1 violation deducts {d1} pp, each D2 "
                        "deducts {d2} pp. A D3 violation burns the entire share of the zone where "
                        "it was recorded. Questions with no violation recorded are treated as met. "
                        "Grade: A — 95% or above with no D2 and no D3; B — 90% or above with no D3 "
                        "and at most one D2; C — two or more D2, or a result below 90%; "
                        "D — at least one D3 violation."),
        "photo_app": "Photo appendix",
        "photo_missing": "Photo not attached",
        "no_photo": "No photo taken",
    },
}
GRADE_COLOR = {"A": "#1E7A45", "B": "#7A6A17", "C": "#B4610F", "D": "#A81E1E"}


def img_tag(path, max_px=1100):
    if not path or not os.path.exists(path):
        return ""
    data = None
    try:
        from PIL import Image
        im = Image.open(path)
        im = im.convert("RGB") if im.mode not in ("RGB", "L") else im
        im.thumbnail((max_px, max_px))
        buf = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        im.save(buf.name, "JPEG", quality=78, optimize=True)
        data = open(buf.name, "rb").read()
        mime = "image/jpeg"
        os.unlink(buf.name)
    except Exception:
        data = open(path, "rb").read()
        mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    b64 = base64.b64encode(data).decode()
    return f'<img src="data:{mime};base64,{b64}" />'


def load_photo_map(path):
    """Карта «ссылка на кадр → файл». Битую карту не проглатываем.

    В боте кадр хранится идентификатором телеграма, а не путём: скачивает его
    и раскладывает по файлам вызывающий, движок лишь получает готовую карту.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as e:
        sys.exit(f"Карта кадров не читается ({path}): {e}")
    if not isinstance(data, dict):
        sys.exit(f"Карта кадров должна быть объектом «ссылка: путь», а не {type(data).__name__}")
    return {str(k): str(v) for k, v in data.items()}


class Photos:
    """Где взять кадр и что нарисовать, если его нет.

    Пустота на месте фотографии — худший исход: партнёр видит нарушение без
    доказательства и справедливо его оспаривает. Поэтому промах всегда даёт
    видимую отметку в отчёте и строку в stderr, а не тихо пропадает.
    """

    def __init__(self, mapping=None):
        #: None — ссылка считается путём (запуск движка руками). Карта задана —
        #: резолвим только по ней: идентификатор телеграма путём не является.
        self.map = mapping
        self.misses = []

    def path(self, src):
        src = str(src or "")
        if self.map is not None:
            return self.map.get(src) or ""
        return src

    def html(self, src, t):
        tag = img_tag(self.path(src))
        if tag:
            return tag
        self.misses.append(str(src or ""))
        return f'<div class="miss">{esc(t["photo_missing"])}</div>'


def shots_html(f, t, src):
    """Разметка кадров одной записи. Кадров нет — пометка «без фотофиксации» (T163).

    Пустое место под текстом партнёр читал как потерянную фотографию и искал
    кадр, которого никогда не было. Решение владельца D074: сказать прямо, на
    чём запись держится.

    Пометку нельзя путать с отметкой промаха (`Photos.html`): та означает, что
    кадр к записи привязан, а файл по ссылке не нашёлся, — это дефект сборки, и
    он остаётся красным. Здесь кадра и не было.

    Пометка появляется ровно там, где показываются кадры: вызов из раздела
    нарушений подчинён режиму `--photos`, вызов из приложения — нет.
    """
    shots = f.get("photos") or ([f["photo"]] if f.get("photo") else [])
    if not shots:
        return [f'<div class="nophoto">{esc(t["no_photo"])}</div>']
    return ['<div class="shots">'] + [src.html(x, t) for x in shots] + ["</div>"]


def clean_q(text):
    """Убирает служебный префикс вида «(D1, D2) » и хвост «(D1)» из формулировки пункта."""
    t = re.sub(r"^\s*\(\s*D[0-9](\s*,\s*D[0-9])*\s*\)\s*", "", str(text or ""))
    t = re.sub(r"\s*\(\s*D[0-9](\s*,\s*D[0-9])*\s*\)\s*$", "", t)
    return t.strip()


def esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


FONT_DIR = os.path.join(HERE, "assets", "fonts")
FONT_FAMILY = "Audit Sans"


def font_css():
    """Кириллический шрифт из проекта, а не из системы.

    Без этого на чистом контейнере вместо букв получаются квадратики, а вид
    отчёта зависит от того, что стоит на машине сборки.
    """
    faces = []
    for name, weight in (("DejaVuSans.ttf", "normal"), ("DejaVuSans-Bold.ttf", "bold")):
        path = os.path.join(FONT_DIR, name)
        if os.path.exists(path):
            faces.append(f'@font-face {{ font-family: "{FONT_FAMILY}"; font-weight: {weight}; '
                         f'font-style: normal; src: url("file://{quote(path)}"); }}')
    if not faces:
        sys.stderr.write(f"ВНИМАНИЕ: шрифта отчёта нет в {FONT_DIR}, "
                         "вид зависит от шрифтов машины\n")
    return "\n".join(faces)


def page_css(t):
    """Страница и её номер — CSS Paged Media, нижний колонтитул `@bottom-center`.

    Подпись берётся из словаря языка: отчёт печатают и подшивают, и английский
    экземпляр не должен получить русское «стр.».
    """
    label = str(t["page"]).replace('"', "")
    return (
        "@page { size: A4; margin: 16mm 14mm 18mm 14mm;\n"
        '  @bottom-center { content: "' + label + ' " counter(page) " / " counter(pages);\n'
        '    font-family: "Audit Sans", "DejaVu Sans", Arial, sans-serif;\n'
        "    font-size: 6.5pt; color: #6F6880; } }\n"
    )


CSS = """
body { font-family: "Audit Sans", "DejaVu Sans", "Helvetica Neue", Arial, sans-serif; color:#23202B; font-size:10.5pt; line-height:1.3; }
h1 { font-size:19pt; margin:0 0 2mm 0; color:#3F2A63; letter-spacing:-.2pt; }
h2 { font-size:12.5pt; margin:6mm 0 2.5mm 0; color:#3F2A63; border-bottom:1.4pt solid #E6E1EF; padding-bottom:1.2mm; page-break-after:avoid; page-break-inside:avoid; }
h2.sec-findings { page-break-before:always; margin-top:0; }
.meta { width:100%; border-collapse:collapse; margin-top:3mm; }
.meta td { padding:0.8mm 0; vertical-align:top; font-size:10pt; line-height:1.15; }
.meta td.k { color:#6F6880; width:38mm; }
.hero { margin:4mm 0 0 0; padding:3.5mm 6mm; border-radius:3mm; background:#F6F4FA; border:1pt solid #E6E1EF; }
.hero td { vertical-align:middle; }
.hero .g { font-size:40pt; font-weight:bold; line-height:1; padding-right:8mm; white-space:nowrap; }
.hero .p { font-size:22pt; font-weight:bold; color:#3F2A63; line-height:1.2; }
.hero .l { color:#6F6880; font-size:9.5pt; text-transform:uppercase; letter-spacing:.5pt; line-height:1.2; }
table.d { width:100%; border-collapse:collapse; margin-top:2.5mm; font-size:9.5pt; line-height:1.2; }
table.d th { background:#F6F4FA; color:#3F2A63; text-align:left; padding:1.3mm 2.5mm; border:.6pt solid #E0DAEA; font-weight:600; }
table.d td { padding:1.3mm 2.5mm; border:.6pt solid #E6E1EF; vertical-align:top; }
table.d td.n { text-align:right; white-space:nowrap; }
tr.z0 td { background:#FCEFEF; }
.badge { display:inline-block; padding:.4mm 2mm; border-radius:1.4mm; color:#fff; font-size:8.5pt; font-weight:bold; }
.D1 { background:#8A8496; } .D2 { background:#C2700F; } .D3 { background:#A81E1E; }
.f { margin:0 0 3mm 0; padding:2.2mm 0 0 0; border-top:.6pt solid #EDE9F3; page-break-inside:avoid; }
.f .h { font-weight:600; }
.f .m { color:#6F6880; font-size:9pt; margin-top:.8mm; }
.f .c { margin-top:1mm; font-size:9.5pt; }
.f .shots, .info .shots { margin-top:1.5mm; }
.f .miss, .info .miss { display:inline-block; box-sizing:border-box; width:78mm; padding:9mm 4mm; margin:0 2mm 2mm 0; text-align:center; color:#A81E1E; background:#FCEFEF; border:1pt dashed #D08A8A; border-radius:1.5mm; font-size:9pt; }
.f .nophoto { display:inline-block; margin-top:1.5mm; padding:.9mm 2.5mm; color:#6F6880; background:#F6F4FA; border:.6pt solid #E0DAEA; border-radius:1.2mm; font-size:8.8pt; }
.f img, .info img { max-width:78mm; max-height:70mm; margin:0 2mm 2mm 0; border:1pt solid #E0DAEA; border-radius:1.5mm; }
.zh { margin:4.5mm 0 1mm 0; font-weight:bold; color:#3F2A63; font-size:11pt; page-break-after:avoid; page-break-inside:avoid; }
.note { color:#6F6880; font-size:8.8pt; margin-top:3mm; line-height:1.35; }
.info p { margin:1.2mm 0; }
.info .k { color:#6F6880; }
"""


def deadline_text(f, t):
    """Срок устранения по нарушению. Письмо на него ссылается — печатаем в отчёте."""
    if f["level"] == "D3" or not f.get("days"):
        return t["immediately"]
    return fmt_date(f.get("due")) or t["immediately"]


def build_html(res, lang, photos, src=None):
    t = T[lang]
    src = Photos() if src is None else src
    m = res["meta"]
    cl = {r["id"]: r for r in load_checklist()}
    qk = "question_en" if lang == "en" else "question_ru"
    pk = "process_en" if lang == "en" else "process_ru"
    zk = "zone_name_en" if lang == "en" else "zone_name_ru"
    nk = "name_en" if lang == "en" else "name_ru"
    g = res["grade"]
    h = [f"<style>{font_css()}\n{page_css(t)}\n{CSS}</style>", f"<h1>{esc(t['title'])}</h1>",
         '<table class="meta">']
    for k, v in ((t["unit"], m.get("unit")), (t["city"], m.get("city")),
                 (t["partner"], m.get("partner")),
                 (t["auditor"], m.get("auditor")), (t["date"], fmt_date(m.get("date")))):
        if v:
            h.append(f'<tr><td class="k">{esc(k)}</td><td>{esc(v)}</td></tr>')
    h.append("</table>")

    label = res["grade_label_en"] if lang == "en" else res["grade_label_ru"]
    h.append(f'''<div class="hero"><table class="hero" style="border:0;padding:0;margin:0"><tr>
      <td class="g" style="color:{GRADE_COLOR.get(g,'#3F2A63')}">{esc(g)}</td>
      <td><div class="l">{esc(t['score'])}</div><div class="p">{res['pct']:g}%</div>
      <div class="l" style="margin-top:1mm">{esc(label)}</div></td></tr></table></div>''')

    c = res["counts"]
    cfg = load_cfg()
    h.append(f"<h2>{esc(t['summary'])}</h2>")
    h.append(f'<table class="d"><tr><th>{esc(t["crit"])}</th><th>{esc(t["count"])}</th><th>{esc(t["cost"])}</th></tr>')
    for lv in ("D1", "D2", "D3"):
        if lv == "D3":
            cost = "-" if not c["D3"] else ", ".join(
                f"{z[nk]} → 0" for z in res["zones"].values() if z["zeroed"])
        else:
            actual = sum(x["cost"] for x in res["findings"] if x["level"] == lv)
            cost = f"−{actual:g}%" if c[lv] else "—"
        h.append(f'<tr><td><span class="badge {lv}">{lv}</span></td><td class="n">{c[lv]}</td><td>{esc(cost)}</td></tr>')
    h.append("</table>")

    h.append(f"<h2>{esc(t['zones'])}</h2>")
    h.append(f'<table class="d"><tr><th>{esc(t["zone"])}</th>'
             f'<th>D1</th><th>D2</th><th>D3</th><th>{esc(t["lost"])}</th><th>{esc(t["left"])}</th></tr>')
    for code, z in res["zones"].items():
        cls = ' class="z0"' if z["zeroed"] else ""
        h.append(f'<tr{cls}><td>{esc(z[nk])}</td>'
                 f'<td class="n">{z["D1"] or ""}</td><td class="n">{z["D2"] or ""}</td>'
                 f'<td class="n">{z["D3"] or ""}</td><td class="n">{z["loss"]:g}%</td>'
                 f'<td class="n">{z.get("score", 0):g}%</td></tr>')
    h.append("</table>")

    h.append(f"<h2 class=\"sec-findings\">{esc(t['findings'])}</h2>")
    VIOL = ("D1", "D2", "D3")
    fs = [f for f in res["findings"] if f["level"] in VIOL]
    notes = [f for f in res["findings"] if f["level"] not in VIOL]
    if not fs:
        h.append(f"<p>{esc(t['no_findings'])}</p>")
    order = {"D3": 0, "D2": 1, "D1": 2}
    by_zone = {}
    for f in fs:
        by_zone.setdefault(f[zk], []).append(f)
    for zname, lst in by_zone.items():
        zeroed = any(x["level"] == "D3" for x in lst)
        suffix = f" — {t['zeroed']}" if zeroed else ""
        h.append(f'<div class="zh">{esc(zname)}{esc(suffix)}</div>')
        for f in sorted(lst, key=lambda x: (order.get(x["level"], 9), x["n"])):
            nc = "" if f["counted"] or f["level"] == "D3" else f' · {t["not_counted"]}'
            h.append('<div class="f">')
            h.append(f'<div class="h"><span class="badge {f["level"]}">{f["level"]}</span> '
                     f'{esc(clean_q(f.get(qk) or f.get("question_ru")))}</div>')
            if f.get("evidence"):
                h.append(f'<div class="c"><b>{esc(t["recorded"])}:</b> {esc(f["evidence"])}</div>')
            if f.get("comment"):
                h.append(f'<div class="c">{esc(t["comment"])}: {esc(f["comment"])}</div>')
            h.append(f'<div class="m">{esc(t["process"])}: {esc(f.get(pk) or "")}{esc(nc)}'
                     f' · {esc(t["deadline"])}: {esc(deadline_text(f, t))}</div>')
            if photos == "all" or (photos == "d2d3" and f["level"] in ("D2", "D3")):
                h.extend(shots_html(f, t, src))
            h.append("</div>")

    # Все заполненные поля идут партнёру. Прежде трое из них (INF01, INF05,
    # INF06) вычёркивались списком HIDDEN_INFO: аудитор их заполнял, а в
    # отчёте они не появлялись — молча. Решение владельца D069: «Собирать и
    # печатать в отчёте» (T159).
    info = dict(res.get("info") or {})
    if notes or info:
        h.append(f'<h2 class="sec-findings">{esc(t["appendix"])}</h2>')
        h.append(f'<div class="note">{esc(t["appendix_note"])}</div>')
        if info:
            h.append('<div class="info">')
            for k, v in info.items():
                q = cl.get(k, {}).get(qk) or cl.get(k, {}).get("question_ru") or k
                # Маркер класса срезаем так же, как у формулировки нарушения
                # ниже: партнёру он не адресован, а до T159 поля не печатались
                # вовсе, и увидеть это было негде.
                поле = info_field(v)
                h.append(f'<p><span class="k">{esc(clean_q(q))}:</span> {esc(поле["text"])}</p>')
                # Кадр поля печатается всегда, как и кадры записей приложения:
                # режим `--photos` управляет разделом нарушений, а приложение
                # показывает доказательства независимо от него (T163).
                #
                # Пометки «Без фотофиксации» (D074) у поля НЕТ намеренно: она
                # отвечает на вопрос «где фотография» у записи, которая обычно
                # с кадром, а информационное поле — это текстовый ответ, и у
                # большинства полей (даты, «да/нет», зоны роста) кадра не
                # бывает по сути. Пометка на каждом из них стала бы шумом и
                # обесценила бы себя там, где значит дело.
                if поле["photos"]:
                    h.append('<div class="shots">'
                             + "".join(src.html(x, t) for x in поле["photos"]) + "</div>")
            h.append("</div>")
        for f in sorted(notes, key=lambda x: x["n"]):
            if True:
                h.append('<div class="f">')
                h.append(f'<div class="h">{esc(clean_q(f.get(qk) or f.get("question_ru")))}</div>')
                if f.get("evidence"):
                    h.append(f'<div class="c">{esc(f["evidence"])}</div>')
                if f.get("comment"):
                    h.append(f'<div class="c">{esc(t["comment"])}: {esc(f["comment"])}</div>')
                h.extend(shots_html(f, t, src))
                h.append("</div>")

    h.append(f'<div class="note"><b>{esc(t["method"])}.</b> '
             + esc(t["method_text"].format(d1=cfg["penalty"]["D1"], d2=cfg["penalty"]["D2"])) + "</div>")
    return "<html><head><meta charset='utf-8'/></head><body>" + "".join(h) + "</body></html>"


PDF_MIN_BYTES = 1000


def pdf_problem(path):
    """Почему собранное не является отчётом. None — отчёт на месте."""
    if not os.path.exists(path):
        return "файл не создан"
    size = os.path.getsize(path)
    if size < PDF_MIN_BYTES:
        return f"файл слишком мал ({size} байт)"
    with open(path, "rb") as f:
        if f.read(5) != b"%PDF-":
            return "собран файл, но это не PDF"
    return None


def html_to_pdf(html, out):
    """Собрать PDF рядом во временный файл и подменить им out только после проверки.

    Провал сборки обязан быть провалом вызова. Раньше успехом считалось само
    наличие файла по пути --out: прошлый отчёт из этой же папки засчитывался
    за собранный, и вызов возвращал 0, ничего не собрав.
    """
    d = os.path.dirname(os.path.abspath(out)) or "."
    if not os.path.isdir(d):
        sys.exit(f"Папки {d} нет — некуда класть отчёт")
    hf = tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8")
    hf.write(html)
    hf.close()
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".report-", suffix=".pdf")
    os.close(fd)
    try:
        try:
            from weasyprint import HTML
        except Exception as e:
            sys.exit(f"Рендерер PDF недоступен: {e}. Нужен WeasyPrint 69.x и системные "
                     f"библиотеки Pango. HTML сохранён: {hf.name}")
        try:
            HTML(filename=hf.name).write_pdf(tmp)
        except Exception as e:
            sys.exit(f"Не удалось собрать PDF: {e}. HTML сохранён: {hf.name}")
        bad = pdf_problem(tmp)
        if bad:
            sys.exit(f"Сборка PDF не дала отчёта: {bad}. HTML сохранён: {hf.name}")
        # mkstemp даёт 0600 — отчёт должен получить обычные права, как любой файл.
        mask = os.umask(0)
        os.umask(mask)
        os.chmod(tmp, 0o666 & ~mask)
        os.replace(tmp, out)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    os.unlink(hf.name)
    return out


# Два шаблона, а не один с условными вставками: при чистой проверке письмо
# не должно требовать план действий по нарушениям, которых нет. Обе боевые
# проверки были именно такими, и оба письма переписывались руками.
LETTER_PLAN = {
    "ru": """Тема: Результаты проверки — {unit}, {date}: оценка {grade} ({pct}%)

Здравствуйте{partner_greet}!

{date} мы провели проверку пиццерии {unit}{city}. Вид проверки — {type}.

Итоговая оценка — {grade} ({pct}%).{grade_note}

Что зафиксировали:
{summary_lines}
{critical_block}Подробный отчёт с фотофиксацией, разбивкой по зонам и сроками устранения по каждому пункту — во вложении.

Просим до {plan_due} прислать план действий по устранению: ответственный и дата по каждому пункту. Нарушения D2 ждём закрытыми в срок, указанный в отчёте, D3 — немедленно, с подтверждением фотографией.

Спасибо за работу. Готовы обсудить любой пункт отчёта и помочь с приоритизацией.

С уважением,
{auditor}
Dodo Brands""",
    "en": """Subject: Inspection results — {unit}, {date}: grade {grade} ({pct}%)

Hello{partner_greet},

On {date} we inspected the {unit} store{city}. Inspection type: {type}.

The final grade is {grade} ({pct}%).{grade_note}

What we recorded:
{summary_lines}
{critical_block}The full report — photo evidence, the breakdown by zone and a deadline for every item — is attached.

Please send us your action plan by {plan_due}, with an owner and a date for each item. D2 violations are expected to be closed within the deadline stated in the report; D3 violations immediately, confirmed with a photo.

Thank you for your work. We are happy to walk through any item in the report and help with prioritisation.

Best regards,
{auditor}
Dodo Brands""",
}
LETTER_CLEAN = {
    "ru": """Тема: Результаты проверки — {unit}, {date}: оценка {grade} ({pct}%)

Здравствуйте{partner_greet}!

{date} мы провели проверку пиццерии {unit}{city}. Вид проверки — {type}.

Итоговая оценка — {grade} ({pct}%).{grade_note} Существенных (D2) и критических (D3) нарушений не зафиксировано.

Что отметили:
{summary_lines}
Подробный отчёт с фотофиксацией, разбивкой по зонам и сроками устранения по каждому пункту — во вложении. Плана действий не ждём: перечисленные отклонения устраняются в рабочем порядке в сроки, указанные в отчёте.

Спасибо за работу. Готовы обсудить любой пункт отчёта.

С уважением,
{auditor}
Dodo Brands""",
    "en": """Subject: Inspection results — {unit}, {date}: grade {grade} ({pct}%)

Hello{partner_greet},

On {date} we inspected the {unit} store{city}. Inspection type: {type}.

The final grade is {grade} ({pct}%).{grade_note} No major (D2) or critical (D3) violations were recorded.

What we noted:
{summary_lines}
The full report — photo evidence, the breakdown by zone and a deadline for every item — is attached. No action plan is expected: the deviations listed above are closed in the normal course of work within the deadlines stated in the report.

Thank you for your work. We are happy to walk through any item in the report.

Best regards,
{auditor}
Dodo Brands""",
}
# Проверка без единой записи. Отдельный шаблон, а не «чистое письмо с нулём»:
# у чистого письма есть блок «Что отметили» и обещание, что перечисленные
# отклонения устраняются в рабочем порядке — при нуле находок и то и другое
# ложь, и партнёр идёт искать в отчёте нарушения, которых нет (T128).
LETTER_EMPTY = {
    "ru": """Тема: Результаты проверки — {unit}, {date}: оценка {grade} ({pct}%)

Здравствуйте{partner_greet}!

{date} мы провели проверку пиццерии {unit}{city}. Вид проверки — {type}.

Итоговая оценка — {grade} ({pct}%).{grade_note} Нарушений не зафиксировано.

Отчёт с разбивкой по зонам — во вложении. Плана действий не ждём.

Спасибо за работу. Готовы обсудить любой пункт отчёта.

С уважением,
{auditor}
Dodo Brands""",
    "en": """Subject: Inspection results — {unit}, {date}: grade {grade} ({pct}%)

Hello{partner_greet},

On {date} we inspected the {unit} store{city}. Inspection type: {type}.

The final grade is {grade} ({pct}%).{grade_note} No violations were recorded.

The report, with the breakdown by zone, is attached. No action plan is expected.

Thank you for your work. We are happy to walk through any item in the report.

Best regards,
{auditor}
Dodo Brands""",
}
GRADE_NOTE = {
    "ru": {"A": " Пиццерия соответствует стандарту.",
           "B": " Пиццерия в целом соответствует стандарту, отклонения незначительные.",
           "C": " Это ниже стандарта и требует плана действий.",
           "D": " Зафиксировано критическое нарушение D3 — оно требует немедленной реакции."},
    "en": {"A": " The store meets the standard.",
           "B": " The store broadly meets the standard; deviations are minor.",
           "C": " This is below standard and requires an action plan.",
           "D": " A critical D3 violation was recorded and requires immediate action."},
}


def summary_lines(res, lang, clean):
    """Сводка нарушений. При чистой проверке перечисляем зоны с D1, а не D2/D3."""
    c = res["counts"]
    zk = "zone_name_en" if lang == "en" else "zone_name_ru"
    qk = "question_en" if lang == "en" else "question_ru"
    if clean:
        lines = [f"— minor D1 deviations: {c['D1']}." if lang == "en"
                 else f"— незначительных отклонений D1: {c['D1']}."]
        shown = ("D1",)
    else:
        lines = [f"— {c['D3']} critical D3, {c['D2']} major D2, {c['D1']} minor D1 violations."
                 if lang == "en"
                 else f"— критических D3: {c['D3']}, значительных D2: {c['D2']}, "
                      f"незначительных D1: {c['D1']}."]
        shown = ("D2", "D3")
    by_zone = {}
    for f in res["findings"]:
        if f["level"] in shown:
            by_zone.setdefault(f[zk], []).append(f)
    for z, lst in by_zone.items():
        names = "; ".join(x.get("evidence") or clean_q(x.get(qk) or x["question_ru"])
                          for x in lst[:3])
        lines.append(f"— {z}: {names}" + (" …" if len(lst) > 3 else ""))
    return "\n".join(lines)


def plan_due_date(res):
    """Срок плана действий: дата отчёта плюс plan_due_days из scoring.json.

    Заглушки `___` здесь больше нет: дату проверки валидирует `inspection_date()`
    до расчёта (T106), поэтому сюда `res` с нечитаемой датой не доходит. Пока
    заглушка была, письмо партнёру уходило с прочерком вместо срока и никто
    об этом не узнавал.
    """
    m = res["meta"]
    cl = {r["id"]: r for r in load_checklist()}
    for k, v in (res.get("info") or {}).items():
        if v and (re.search(r"план\w* действий", (cl.get(k, {}).get("question_ru") or "").lower())
                  or "action plan" in (cl.get(k, {}).get("question_en") or "").lower()):
            return str(info_field(v)["text"])
    days = int(load_cfg().get("plan_due_days", 10))
    return (inspection_date(res) + timedelta(days=days)).isoformat()


def inspection_kind(meta, lang):
    """Вид проверки словом на языке ПЕЧАТИ, а не на языке заведения проверки (T177).

    Код — сущность, слово — перевод, и подставляется он здесь. Раньше слово
    записывалось в проверку при её заведении, по языку отчёта того дня, а письмо
    на другом языке переводилось сопоставлением строк (`TYPE_EN`) с молчаливым
    возвратом исходника: за одним видом стояло два разных английских слова, и
    одно уходило партнёру.

    Проверка, заведённая до этой правки, хранит вместо кода готовое слово. На её
    собственном языке оно печатается как записано — документ не меняется ни в
    знаке. На чужом языке — отказ: подставить в английское письмо слово,
    записанное по-русски, значит повторить ровно тот дефект, ради которого всё
    затевалось, а угадать код по формулировке нельзя — формулировки правятся и
    переводятся, коды нет. Чинится одной командой, и она названа в отказе.
    """
    code = (meta.get("kind") or "").strip()
    if code:
        return kind_title(code, lang)
    word = (meta.get("type") or "").strip()
    if not word:
        return ""
    recorded = (meta.get("lang") or "ru").strip().lower()
    if recorded != lang:
        sys.exit(
            f"Вид проверки записан словом «{word}» на языке «{recorded}», а документ "
            f"собирается на «{lang}» — перевести нечем: слово это не код. "
            f"Свяжите вид проверки кодом: audit.py meta --kind КОД "
            f"(коды показывает audit.py kinds)"
        )
    return word


def build_letter(res, lang):
    m = res["meta"]
    c = res["counts"]
    zk = "zone_name_en" if lang == "en" else "zone_name_ru"
    qk = "question_en" if lang == "en" else "question_ru"
    clean = not c["D2"] and not c["D3"]
    crit = ""
    d3 = [f for f in res["findings"] if f["level"] == "D3"]
    if d3:
        names = "; ".join((f.get("evidence") or clean_q(f.get(qk) or f["question_ru"]))
                          + f" — {f[zk]}" for f in d3)
        if lang == "en":
            crit = ("\nCritical (D3): " + names + ". Per our methodology a D3 violation zeroes "
                    "the whole zone where it was found.\n\n")
        else:
            crit = ("\nКритично (D3): " + names + ". По методике нарушение D3 обнуляет всю зону, "
                    "в которой оно зафиксировано.\n\n")
    contact = m.get("contact") or ""
    if clean and not res["findings"]:
        template = LETTER_EMPTY[lang]
    else:
        template = LETTER_CLEAN[lang] if clean else LETTER_PLAN[lang]
    return template.format(
        unit=m.get("unit", ""), date=fmt_date(m.get("date")),
        city=(f", {m['city']}" if m.get("city") else ""),
        type=inspection_kind(m, lang),
        grade=res["grade"], pct=f"{res['pct']:g}",
        grade_note=GRADE_NOTE[lang].get(res["grade"], ""),
        summary_lines=summary_lines(res, lang, clean), critical_block=crit,
        plan_due=fmt_date(plan_due_date(res)),
        partner_greet=(f", {contact}" if contact else ""),
        auditor=m.get("auditor") or "___")


def letter_problems(text, res):
    """Письмо уходит партнёру: пустое или без ключевых полей — это провал, не успех."""
    m = res["meta"]
    bad = []
    if not text.strip():
        bad.append("текст пустой")
    if not (m.get("unit") or "").strip():
        bad.append("не указана пиццерия")
    if not (m.get("date") or "").strip():
        bad.append("не указана дата проверки")
    if not res.get("grade"):
        bad.append("нет оценки")
    return bad


def fmt_date(s):
    """ISO-дату 2026-08-21 показываем как 21.08.2026. Не-даты возвращаем как есть."""
    try:
        return date.fromisoformat(str(s)).strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return str(s or "")


def default_name(meta, lang=None):
    """Имя файла: <Аудит|Audit> <пиццерия> - <аудитор> - <дд.мм.гггг>.pdf.

    Слово берётся по языку СОБИРАЕМОГО отчёта, а не по языку в шапке проверки:
    `--lang en` на проверке, заведённой по-русски, обязан дать английское имя,
    иначе демо, которое обязано быть англоязычным целиком, отдаёт файл с русским
    словом в имени (задача T100). Русское имя не тронуто: партнёры получают файл
    ровно с тем именем, что и раньше.
    """
    def clean(v, fallback):
        v = re.sub(r'[\\/:*?"<>|]', "-", str(v or "")).strip(" .")
        return re.sub(r"\s+", " ", v) or fallback
    effective = lang or meta.get("lang") or "ru"
    word = "Audit" if str(effective).lower().startswith("en") else "Аудит"
    parts = [word + " " + clean(meta.get("unit"), "пиццерия")]
    if meta.get("auditor"):
        parts.append(clean(meta.get("auditor"), ""))
    if meta.get("date"):
        parts.append(fmt_date(meta.get("date")))
    return " - ".join(p for p in parts if p) + ".pdf"


def report_path(out, out_dir, meta, lang):
    """Куда класть собранный отчёт.

    Раньше отчёт без `--out` ложился в рабочий каталог — то есть рядом с
    состоянием проверки, а в `examples/` ещё и рядом с эталонным отчётом того
    же имени. Ручной смоук сборки затирал эталон молча, и 02.09.2026 именно
    это и произошло: восстановить его неоткуда, `examples/` вне git (D002).
    Поэтому по умолчанию отчёт уходит в отдельный каталог `reports/` рядом с
    состоянием. Явный `--out` сильнее: человек назвал путь сам.
    """
    if out and out_dir:
        sys.exit("--out и --out-dir указаны вместе — не понять, куда класть отчёт. "
                 "--out задаёт путь целиком, --out-dir только каталог; оставьте один")
    if out:
        return out
    d = out_dir or os.path.join(state_dir(), REPORT_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, default_name(meta, lang))


def report_lang(asked, meta):
    """Язык собираемого документа: `--lang`, иначе язык из шапки, иначе русский.

    Незнакомый язык — отказ с ненулевым кодом (T175). Раньше он молча заменялся
    русским, и вызов заканчивался нулём: письмо, запрошенное на немецком,
    приходило русским, и узнать об этом можно было, только прочитав результат.
    Опечатка в коде языка отправляла партнёру не тот документ.

    Шапка проверки проверяется наравне с аргументом, хотя язык в ней валидирует
    `init`/`meta`: состояние — обычный JSON на диске, и в расчёт приходят
    проверки, начатые до появления валидации шапки, и файлы, поправленные
    руками. Тот же довод, что и у нечитаемой даты проверки (T106).
    """
    raw = asked if asked is not None else meta.get("lang")
    lang = str(raw or "ru").strip().lower()
    if lang not in T:
        where = ("передан в --lang" if asked is not None
                 else "записан в шапке проверки; поправить: audit.py meta --lang")
        sys.exit(f"Язык «{raw}» не заведён — {where}. Доступны: {', '.join(T)}. "
                 f"Молчаливой замены на русский больше нет: партнёр получил бы "
                 f"документ не на том языке, которым его просили")
    return lang


def main():
    p = argparse.ArgumentParser()
    s = p.add_subparsers(dest="cmd", required=True)
    a1 = s.add_parser("pdf"); a1.add_argument("--out"); a1.add_argument("--lang"); a1.add_argument("--photos", default="all")
    a1.add_argument("--out-dir", help="каталог для собранного отчёта; по умолчанию reports/ рядом с состоянием")
    a1.add_argument("--photo-map", help="JSON-карта «ссылка на кадр: путь к файлу»")
    a2 = s.add_parser("letter"); a2.add_argument("--lang")
    a3 = s.add_parser("html"); a3.add_argument("--out"); a3.add_argument("--lang")
    a3.add_argument("--photos", default="all")
    a3.add_argument("--photo-map", help="JSON-карта «ссылка на кадр: путь к файлу»")
    a = p.parse_args()
    st = load_state()
    # Язык проверяется до расчёта: он пришёл из командной строки, и отвечать
    # на промах разбором методики значит показать человеку не ту причину.
    lang = report_lang(a.lang, st["meta"])
    res = compute(st, load_checklist(), load_zones(), load_cfg())
    if a.cmd == "letter":
        text = build_letter(res, lang)
        bad = letter_problems(text, res)
        if bad:
            sys.exit("Письмо не собрано: " + "; ".join(bad))
        print(text)
        return
    src = Photos(load_photo_map(a.photo_map) if a.photo_map else None)
    html = build_html(res, lang, a.photos, src)
    # Промах кадра не отменяет отчёта, но обязан быть услышан: в отчёте стоит
    # видимая отметка, здесь — строка о каждом пропавшем кадре. Молча выкинуть
    # доказательство нельзя, а решать, отдавать ли такой отчёт партнёру,
    # вызывающему проще: он знает, закончена ли проверка на точке.
    for miss in src.misses:
        print(f"Кадр не найден и в отчёт не попал: {miss or '(пустая ссылка)'}", file=sys.stderr)
    if a.cmd == "html":
        if a.out:
            with open(a.out, "w", encoding="utf-8") as f:
                f.write(html)
            print(a.out)
        else:
            print(html)
        return
    out = report_path(a.out, getattr(a, "out_dir", None), st["meta"], lang)
    html_to_pdf(html, out)
    print(out)


if __name__ == "__main__":
    main()
