"""T035: замер точности разбора на боевых данных — число, а не мнение.

Прогоняет набор `bench_dataset.load_cases()` (31 кадр с известным ответом:
код, класс, зона) через `classify()` ровно так, как это делает бот по кнопке
«Разобрать» (T064) — без комментария, с зоной-подсказкой, которая
соответствует памяти последней зоны (D048): `zone_hint = case.zone`. Один и
тот же набор кадров прогоняется по трём моделям — флагман, средняя, дешёвая —
чтобы сравнение было честным, а не «первое, что попробовали».

Сеть здесь — не побочный эффект, а смысл модуля: без реального вызова числа
были бы мнением. `run_model`/`run_case` делают вызовы, `summarize` — чистая
функция без сети, её тестирует `tests/test_recognize_bench_run.py` на
фальшивых результатах.

Запуск (из корня репозитория, с настоящим `OPENAI_API_KEY` в окружении):

    .venv/bin/python tools/bench_run.py --examples examples --out reports/bench.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.recognize.classify import classify
from src.recognize.errors import RecognizeError
from tools.bench_dataset import BenchCase, load_cases

#: Флагман — конфиг блока (D013). Средняя и дешёвая — соседи по стоимости:
#: `gpt-4o-mini` явно названа в D013 как отвергнутый дешёвый вариант,
#: `gpt-5.4-mini` — ближайшая по поколению модель среднего размера, доступная
#: в каталоге на момент замера (проверено вызовом `client.models.list()`,
#: журнал блока). Порядок — от дорогой к дешёвой, он же порядок в отчёте.
DEFAULT_MODELS = ("gpt-5.6-sol", "gpt-5.4-mini", "gpt-4o-mini")

#: Сколько запросов идёт одновременно. Больше — быстрее, но ближе к лимиту
#: провайдера на параллельные запросы; это разовый замер, не боевая нагрузка.
CONCURRENCY = 6


@dataclass(frozen=True)
class CaseResult:
    """Один запрос: ожидаемый ответ против того, что вернул `classify`."""

    model: str
    case_id: str
    zone: str
    expected_code: str
    expected_level: str
    got_code: str | None
    got_level: str | None
    got_zone: str | None
    code_correct: bool
    exact_correct: bool  # код И класс совпали
    confidence: float
    needs_human: bool
    usage: dict[str, int]
    error: str = ""


def run_case(case: BenchCase, model: str) -> CaseResult:
    """Один вызов `classify` — ровно то, что бот делает по кнопке «Разобрать».

    Отказ модели (`RecognizeError`) не прерывает замер — он такой же
    измеримый исход, как неверный код: превращается в строку `results` с
    пустым ответом и текстом ошибки, а не роняет прогон остальных кадров.
    """
    try:
        suggestion = classify("", photo=case.photo.read_bytes(), zone_hint=case.zone, model=model)
    except RecognizeError as exc:
        return CaseResult(
            model=model,
            case_id=case.case_id,
            zone=case.zone,
            expected_code=case.code or "",
            expected_level=case.level or "",
            got_code=None,
            got_level=None,
            got_zone=None,
            code_correct=False,
            exact_correct=False,
            confidence=0.0,
            needs_human=True,
            usage={},
            error=str(exc),
        )
    top = suggestion.top()
    got_code = top.code if top else None
    got_level = top.level if top else None
    return CaseResult(
        model=model,
        case_id=case.case_id,
        zone=case.zone,
        expected_code=case.code or "",
        expected_level=case.level or "",
        got_code=got_code,
        got_level=got_level,
        got_zone=top.zone if top else None,
        code_correct=got_code == case.code,
        exact_correct=got_code == case.code and got_level == case.level,
        confidence=top.confidence if top else 0.0,
        needs_human=suggestion.needs_human,
        usage=dict(suggestion.usage),
    )


@dataclass(frozen=True)
class ModelSummary:
    """Числа T035 требует: точность, а не мнение."""

    model: str
    cases: int
    code_accuracy: float
    exact_accuracy: float
    needs_human_rate: float
    errors: int
    input_tokens_avg: float
    output_tokens_avg: float


def summarize(results: list[CaseResult]) -> list[ModelSummary]:
    """Числа по модели. Чистая функция — без сети, тестируется на фальшивых данных."""
    by_model: dict[str, list[CaseResult]] = {}
    for r in results:
        by_model.setdefault(r.model, []).append(r)

    out: list[ModelSummary] = []
    for model in dict.fromkeys(r.model for r in results):  # порядок первого появления
        rs = by_model[model]
        n = len(rs)
        errors = sum(1 for r in rs if r.error)
        ok = [r for r in rs if not r.error]
        out.append(
            ModelSummary(
                model=model,
                cases=n,
                code_accuracy=sum(r.code_correct for r in rs) / n if n else 0.0,
                exact_accuracy=sum(r.exact_correct for r in rs) / n if n else 0.0,
                needs_human_rate=sum(r.needs_human for r in rs) / n if n else 0.0,
                errors=errors,
                input_tokens_avg=(
                    sum(r.usage.get("input", 0) for r in ok) / len(ok) if ok else 0.0
                ),
                output_tokens_avg=(
                    sum(r.usage.get("output", 0) for r in ok) / len(ok) if ok else 0.0
                ),
            )
        )
    return out


def _print_progress(done: int, total: int, started: float) -> None:
    elapsed = time.monotonic() - started
    print(f"[{done}/{total}] {elapsed:.0f}s", flush=True)


def run_models(cases: list[BenchCase], models: tuple[str, ...]) -> list[CaseResult]:
    """Все кадры на всех моделях, с прогрессом — чтобы не молчать долгими минутами."""
    jobs = [(case, model) for model in models for case in cases]
    results: list[CaseResult] = []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = {pool.submit(run_case, case, model): (case, model) for case, model in jobs}
        for i, future in enumerate(as_completed(futures), start=1):
            results.append(future.result())
            if i % 5 == 0 or i == len(jobs):
                _print_progress(i, len(jobs), started)
    return results


def _table(summaries: list[ModelSummary]) -> str:
    lines = [
        "| Модель | Кадров | Код верный | Код+класс верно | Нужен человек | Ошибок | "
        "Токены вход/выход (среднее) |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(
            f"| `{s.model}` | {s.cases} | {s.code_accuracy:.0%} | {s.exact_accuracy:.0%} | "
            f"{s.needs_human_rate:.0%} | {s.errors} | "
            f"{s.input_tokens_avg:.0f} / {s.output_tokens_avg:.0f} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examples", type=Path, default=Path("examples"))
    parser.add_argument("--out", type=Path, default=Path("reports/bench.json"))
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    args = parser.parse_args(argv)

    cases = load_cases(args.examples)
    print(
        f"{len(cases)} кадров × {len(args.models)} моделей = "
        f"{len(cases) * len(args.models)} запросов",
        flush=True,
    )

    results = run_models(cases, tuple(args.models))
    summaries = summarize(results)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "results": [asdict(r) for r in results],
                "summary": [asdict(s) for s in summaries],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print()
    print(_table(summaries))
    print(f"\nПодробности: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
