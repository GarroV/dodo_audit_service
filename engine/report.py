#!/usr/bin/env python3
"""Собирает итоговый PDF-отчёт и черновик письма партнёру из inspection.json.

  report.py pdf  [--out отчёт.pdf] [--lang ru|en] [--photos all|d2d3|none]
  report.py letter [--lang ru|en]      печатает текст письма в stdout

PDF собирается через HTML: wkhtmltopdf → chromium → weasyprint, что найдётся первым.
"""
import argparse, base64, json, mimetypes, os, re, shutil, subprocess, sys, tempfile
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from audit import load_checklist, load_zones, load_cfg, load_state, compute  # noqa: E402

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
    },
}
TYPE_EN = {"Плановая": "Scheduled", "Повторная": "Follow-up", "Внеплановая": "Unscheduled",
           "Платная после комитета": "Paid, post-committee"}
HIDDEN_INFO = {"INF01", "INF05", "INF06"}  # не печатаются в отчёте и не спрашиваются
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


def clean_q(text):
    """Убирает служебный префикс вида «(D1, D2) » и хвост «(D1)» из формулировки пункта."""
    t = re.sub(r"^\s*\(\s*D[0-9](\s*,\s*D[0-9])*\s*\)\s*", "", str(text or ""))
    t = re.sub(r"\s*\(\s*D[0-9](\s*,\s*D[0-9])*\s*\)\s*$", "", t)
    return t.strip()


def esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


CSS = """
@page { size: A4; margin: 16mm 14mm 18mm 14mm; }
body { font-family: "DejaVu Sans", "Helvetica Neue", Arial, sans-serif; color:#23202B; font-size:10.5pt; line-height:1.45; }
h1 { font-size:19pt; margin:0 0 2mm 0; color:#3F2A63; letter-spacing:-.2pt; }
h2 { font-size:12.5pt; margin:9mm 0 3mm 0; color:#3F2A63; border-bottom:1.4pt solid #E6E1EF; padding-bottom:1.5mm; page-break-after:avoid; page-break-inside:avoid; }
h2.sec-findings { page-break-before:always; margin-top:0; }
.meta { width:100%; border-collapse:collapse; margin-top:4mm; }
.meta td { padding:1.3mm 0; vertical-align:top; font-size:10pt; }
.meta td.k { color:#6F6880; width:38mm; }
.hero { margin:6mm 0 0 0; padding:5mm 6mm; border-radius:3mm; background:#F6F4FA; border:1pt solid #E6E1EF; }
.hero td { vertical-align:middle; }
.hero .g { font-size:40pt; font-weight:bold; line-height:1; padding-right:8mm; white-space:nowrap; }
.hero .p { font-size:22pt; font-weight:bold; color:#3F2A63; }
.hero .l { color:#6F6880; font-size:9.5pt; text-transform:uppercase; letter-spacing:.5pt; }
table.d { width:100%; border-collapse:collapse; margin-top:3mm; font-size:9.5pt; }
table.d th { background:#F6F4FA; color:#3F2A63; text-align:left; padding:2mm 2.5mm; border:.6pt solid #E0DAEA; font-weight:600; }
table.d td { padding:2mm 2.5mm; border:.6pt solid #E6E1EF; vertical-align:top; }
table.d td.n { text-align:right; white-space:nowrap; }
tr.z0 td { background:#FCEFEF; }
.badge { display:inline-block; padding:.4mm 2mm; border-radius:1.4mm; color:#fff; font-size:8.5pt; font-weight:bold; }
.D1 { background:#8A8496; } .D2 { background:#C2700F; } .D3 { background:#A81E1E; }
.f { margin:0 0 4mm 0; padding:3mm 0 0 0; border-top:.6pt solid #EDE9F3; page-break-inside:avoid; }
.f .h { font-weight:600; }
.f .m { color:#6F6880; font-size:9pt; margin-top:.8mm; }
.f .c { margin-top:1.2mm; font-size:9.5pt; }
.f .shots { margin-top:2mm; }
.f img { max-width:78mm; max-height:70mm; margin:0 2mm 2mm 0; border:1pt solid #E0DAEA; border-radius:1.5mm; }
.zh { margin:6mm 0 1mm 0; font-weight:bold; color:#3F2A63; font-size:11pt; page-break-after:avoid; page-break-inside:avoid; }
.note { color:#6F6880; font-size:8.8pt; margin-top:4mm; line-height:1.5; }
.info p { margin:1.5mm 0; }
.info .k { color:#6F6880; }
"""


def build_html(res, lang, photos):
    t = T[lang]
    m = res["meta"]
    itype = m.get("type", "")
    if lang == "en":
        itype = TYPE_EN.get(itype, itype)
    cl = {r["id"]: r for r in load_checklist()}
    qk = "question_en" if lang == "en" else "question_ru"
    pk = "process_en" if lang == "en" else "process_ru"
    zk = "zone_name_en" if lang == "en" else "zone_name_ru"
    nk = "name_en" if lang == "en" else "name_ru"
    g = res["grade"]
    h = [f"<style>{CSS}</style>", f"<h1>{esc(t['title'])}</h1>", '<table class="meta">']
    for k, v in ((t["unit"], m.get("unit")), (t["city"], m.get("city")),
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
            h.append(f'<div class="m">{esc(t["process"])}: {esc(f.get(pk) or "")}{esc(nc)}</div>')
            if photos == "all" or (photos == "d2d3" and f["level"] in ("D2", "D3")):
                shots = f.get("photos") or ([f["photo"]] if f.get("photo") else [])
                if shots:
                    h.append('<div class="shots">')
                    for src in shots:
                        h.append(img_tag(src))
                    h.append("</div>")
            h.append("</div>")

    info = {k: v for k, v in (res.get("info") or {}).items() if k not in HIDDEN_INFO}
    if notes or info:
        h.append(f'<h2 class="sec-findings">{esc(t["appendix"])}</h2>')
        h.append(f'<div class="note">{esc(t["appendix_note"])}</div>')
        if info:
            h.append('<div class="info">')
            for k, v in info.items():
                q = cl.get(k, {}).get(qk) or cl.get(k, {}).get("question_ru") or k
                h.append(f'<p><span class="k">{esc(q)}:</span> {esc(v)}</p>')
            h.append("</div>")
        for f in sorted(notes, key=lambda x: x["n"]):
            if True:
                h.append('<div class="f">')
                h.append(f'<div class="h">{esc(clean_q(f.get(qk) or f.get("question_ru")))}</div>')
                if f.get("evidence"):
                    h.append(f'<div class="c">{esc(f["evidence"])}</div>')
                if f.get("comment"):
                    h.append(f'<div class="c">{esc(t["comment"])}: {esc(f["comment"])}</div>')
                shots = f.get("photos") or ([f["photo"]] if f.get("photo") else [])
                if shots:
                    h.append('<div class="shots">')
                    for src in shots:
                        h.append(img_tag(src))
                    h.append("</div>")
                h.append("</div>")

    h.append(f'<div class="note"><b>{esc(t["method"])}.</b> '
             + esc(t["method_text"].format(d1=cfg["penalty"]["D1"], d2=cfg["penalty"]["D2"])) + "</div>")
    return "<html><head><meta charset='utf-8'/></head><body>" + "".join(h) + "</body></html>"


def html_to_pdf(html, out):
    hf = tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8")
    hf.write(html); hf.close()
    if shutil.which("wkhtmltopdf"):
        r = subprocess.run(["wkhtmltopdf", "--enable-local-file-access", "--encoding", "utf-8",
                            "--margin-top", "16mm", "--margin-bottom", "18mm",
                            "--margin-left", "14mm", "--margin-right", "14mm",
                            "--footer-font-size", "7", "--footer-right", "[page]/[topage]",
                            hf.name, out], capture_output=True, text=True)
        if os.path.exists(out) and os.path.getsize(out) > 1000:
            os.unlink(hf.name); return out
        sys.stderr.write(r.stderr[-800:] + "\n")
    for chrome in ("chromium", "chromium-browser", "google-chrome",
                   "/opt/pw-browsers/chromium/chrome-linux/chrome"):
        p = shutil.which(chrome) if not chrome.startswith("/") else (chrome if os.path.exists(chrome) else None)
        if p:
            subprocess.run([p, "--headless", "--disable-gpu", "--no-sandbox",
                            f"--print-to-pdf={out}", "--no-pdf-header-footer", f"file://{hf.name}"],
                           capture_output=True)
            if os.path.exists(out):
                os.unlink(hf.name); return out
    try:
        from weasyprint import HTML
        HTML(filename=hf.name).write_pdf(out)
        os.unlink(hf.name); return out
    except Exception as e:
        sys.exit(f"Не удалось собрать PDF: {e}. HTML сохранён: {hf.name}")


LETTER = {
    "ru": """Тема: Результаты проверки — {unit}, {date}: оценка {grade} ({pct}%)

Здравствуйте{partner_greet}!

{date} мы провели проверку пиццерии {unit}{city}. Вид проверки — {type}.

Итоговая оценка — {grade} ({pct}%).{grade_note}

Что зафиксировали:
{summary_lines}
{critical_block}Подробный отчёт с фотофиксацией, разбивкой по зонам и сроками устранения по каждому пункту — во вложении.

Просим до {plan_due} прислать план действий по устранению: ответственный и дата по каждому пункту. Нарушения D2 ждём закрытыми в срок, указанный в отчёте, D3 — немедленно, с подтверждением фотографией.

{call_block}Спасибо за работу. Готовы обсудить любой пункт отчёта и помочь с приоритизацией.

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

{call_block}Thank you for your work. We are happy to walk through any item in the report and help with prioritisation.

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


def build_letter(res, lang):
    m = res["meta"]; c = res["counts"]
    zk = "zone_name_en" if lang == "en" else "zone_name_ru"
    qk = "question_en" if lang == "en" else "question_ru"
    if lang == "en":
        lines = [f"— {c['D3']} critical D3, {c['D2']} major D2, {c['D1']} minor D1 violations."]
    else:
        lines = [f"— критических D3: {c['D3']}, значительных D2: {c['D2']}, незначительных D1: {c['D1']}."]
    worst = {}
    for f in res["findings"]:
        if f["level"] in ("D2", "D3"):
            worst.setdefault(f[zk], []).append(f)
    for z, lst in worst.items():
        names = "; ".join(x.get("evidence") or clean_q(x.get(qk) or x["question_ru"]) for x in lst[:3])
        lines.append(f"— {z}: {names}" + (" …" if len(lst) > 3 else ""))
    crit = ""
    d3 = [f for f in res["findings"] if f["level"] == "D3"]
    if d3:
        if lang == "en":
            crit = ("\nCritical (D3): " + "; ".join((f.get("evidence") or clean_q(f.get(qk) or f["question_ru"])) + f" — {f[zk]}" for f in d3)
                    + ". Per our methodology a D3 violation zeroes the whole zone where it was found.\n\n")
        else:
            crit = ("\nКритично (D3): " + "; ".join((f.get("evidence") or clean_q(f.get(qk) or f["question_ru"])) + f" — {f[zk]}" for f in d3)
                    + ". По методике нарушение D3 обнуляет всю зону, в которой оно зафиксировано.\n\n")
    cl = {r["id"]: r for r in load_checklist()}
    call, plan_due = "", ""
    for k, v in (res.get("info") or {}).items():
        if v and (re.search(r"план\w* действий", (cl.get(k, {}).get("question_ru") or "").lower())
                  or "action plan" in (cl.get(k, {}).get("question_en") or "").lower()):
            plan_due = str(v)
    if not plan_due:
        days = int(load_cfg().get("plan_due_days", 10))
        try:
            plan_due = (date.fromisoformat(m["date"]) + timedelta(days=days)).isoformat()
        except Exception:
            plan_due = "___"
    contact = m.get("contact") or ""
    return LETTER[lang].format(
        unit=m.get("unit", ""), date=fmt_date(m.get("date")),
        city=(f", {m['city']}" if m.get("city") else ""),
        type=(TYPE_EN.get(m.get("type", ""), m.get("type", "")) if lang == "en" else m.get("type", "")),
        grade=res["grade"], pct=f"{res['pct']:g}",
        grade_note=GRADE_NOTE[lang].get(res["grade"], ""),
        summary_lines="\n".join(lines), critical_block=crit, call_block=call,
        plan_due=fmt_date(plan_due), partner_greet=(f", {contact}" if contact else ""),
        auditor=m.get("auditor") or "___")


def fmt_date(s):
    """ISO-дату 2026-08-21 показываем как 21.08.2026. Не-даты возвращаем как есть."""
    try:
        return date.fromisoformat(str(s)).strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return str(s or "")


def default_name(meta):
    """Имя файла: Аудит <пиццерия> - <аудитор> - <дд.мм.гггг>.pdf, одинаково для RU и EN."""
    def clean(v, fallback):
        v = re.sub(r'[\\/:*?"<>|]', "-", str(v or "")).strip(" .")
        return re.sub(r"\s+", " ", v) or fallback
    parts = ["Аудит " + clean(meta.get("unit"), "пиццерия")]
    if meta.get("auditor"):
        parts.append(clean(meta.get("auditor"), ""))
    if meta.get("date"):
        parts.append(fmt_date(meta.get("date")))
    return " - ".join(p for p in parts if p) + ".pdf"


def main():
    p = argparse.ArgumentParser()
    s = p.add_subparsers(dest="cmd", required=True)
    a1 = s.add_parser("pdf"); a1.add_argument("--out"); a1.add_argument("--lang"); a1.add_argument("--photos", default="all")
    a2 = s.add_parser("letter"); a2.add_argument("--lang")
    a = p.parse_args()
    st = load_state()
    res = compute(st, load_checklist(), load_zones(), load_cfg())
    lang = a.lang or st["meta"].get("lang") or "ru"
    if lang not in T:
        lang = "ru"
    if a.cmd == "letter":
        print(build_letter(res, lang)); return
    out = a.out or default_name(st["meta"])
    html_to_pdf(build_html(res, lang, a.photos), out)
    print(out)


if __name__ == "__main__":
    main()
