#!/usr/bin/env python3
"""Живой замер: настоящие вызовы модели, настоящие кадры (T101, решение D062).

Прошлый замер (`tools/loadcheck.py`) мерил обработку и честно об этом говорил:
настоящая сеть и настоящая модель в него не входили. Владелец попросил замерить
на живом — «нам надо реально понять ограничения системы и знать их на будущее».

**Это платная операция.** Каждый вызов уходит провайдеру и стоит денег, поэтому:

- число вызовов задаётся явно и по умолчанию мало́;
- стоимость печатается по факту, из расхода токенов, который возвращает сам
  провайдер, — а не прикидывается;
- без `--yes` скрипт ничего не вызывает, только показывает план и цену.

Меряется то, чего не видно без сети:

1. **Задержка одного разбора** — сколько аудитор ждёт после отправки кадра.
2. **Очередь**: N разборов одновременно — растёт ли задержка линейно, и на
   каком числе провайдер начинает отвечать отказом.
3. **Поведение на отказе** — деградация в ручной выбор должна работать
   (T034), а не ронять проверку.

Запуск:  python tools/loadcheck_live.py --calls 6 --parallel 3 --yes
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.recognize.classify import classify  # noqa: E402
from src.recognize.config import load_recognize_settings  # noqa: E402
from src.recognize.errors import ModelUnavailable  # noqa: E402

#: Цена вызова, замеренная 28.08.2026 на этом же продукте. Служит только для
#: оценки ПЛАНА до запуска; по факту стоимость считается из расхода токенов.
ЦЕНА_ВЫЗОВА_ОЦЕНКА = 0.0146

#: Комментарии аудитора к боевым кадрам — настоящие формулировки с точки.
ЗАМЕТКИ = (
    "нагар под конвейерной лентой печи в горячем цеху",
    "продукт без маркировки в холодильнике",
    "пол в зале грязный, разводы у входа",
    "мусорный бак у фасада переполнен",
    "тесто на столе без укрытия",
    "раковина для мытья рук без мыла",
)


def _кадры(limit: int) -> list[Path]:
    frames = sorted(ROOT.glob("examples/*/photos/*.jpg"))
    if not frames:
        raise SystemExit("нет боевых кадров в examples/*/photos — замерять нечего")
    return [frames[i % len(frames)] for i in range(limit)]


def _один(номер: int, кадр: Path, заметка: str) -> dict[str, object]:
    начало = time.perf_counter()
    try:
        ответ = classify(заметка, photo=кадр.read_bytes(), lang="ru")
        прошло = time.perf_counter() - начало
        return {
            "n": номер,
            "сек": прошло,
            "ошибка": None,
            "кандидатов": len(ответ.candidates),
            "токенов": dict(ответ.usage or {}),
        }
    except ModelUnavailable as exc:
        return {"n": номер, "сек": time.perf_counter() - начало, "ошибка": str(exc)[:90]}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--calls", type=int, default=6, help="сколько вызовов модели сделать")
    p.add_argument("--parallel", type=int, default=3, help="сколько одновременно")
    p.add_argument("--yes", action="store_true", help="подтвердить платный прогон")
    a = p.parse_args()

    settings = load_recognize_settings()
    план = a.calls * ЦЕНА_ВЫЗОВА_ОЦЕНКА
    print(f"Модель: {settings.model}")
    print(f"План: {a.calls} вызовов, по {a.parallel} одновременно. Оценка расхода: ${план:.2f}")
    if not a.yes:
        print("\nЭто платный прогон. Без --yes ничего не вызывается.")
        return 0

    кадры = _кадры(a.calls)
    начало = time.perf_counter()
    результаты: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=a.parallel) as pool:
        futures = [
            pool.submit(_один, i, кадры[i], ЗАМЕТКИ[i % len(ЗАМЕТКИ)]) for i in range(a.calls)
        ]
        for f in as_completed(futures):
            r = f.result()
            результаты.append(r)
            метка = "ОТКАЗ" if r["ошибка"] else f"{r['кандидатов']} кандидатов"
            print(f"  #{r['n']:2}  {r['сек']:5.2f} с  {метка}")
    всего = time.perf_counter() - начало

    удачные = [r for r in результаты if not r["ошибка"]]
    отказы = [r for r in результаты if r["ошибка"]]
    сроки = sorted(float(r["сек"]) for r in удачные)
    print(f"\nОбщее время: {всего:.1f} с на {a.calls} вызовов по {a.parallel} одновременно")
    if сроки:
        print(
            f"Задержка разбора: медиана {statistics.median(сроки):.2f} с, "
            f"минимум {сроки[0]:.2f} с, максимум {сроки[-1]:.2f} с"
        )
        print(f"Пропускная способность: {len(удачные) / всего:.2f} разбора в секунду")
    if отказы:
        print(f"\nОтказов: {len(отказы)} из {a.calls}")
        for r in отказы[:3]:
            print(f"  {r['ошибка']}")
        print("Отказ не роняет проверку: бот уходит в ручной выбор пункта (T034).")
    else:
        print("Отказов нет.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
