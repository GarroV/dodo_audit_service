"""T035: числа замера точности — без сети.

`summarize` — чистая функция: она не звонит модели, а сворачивает уже
полученные `CaseResult` в проценты по каждой модели. Сеть проверена отдельно,
вручную (журнал блока): здесь — то, что можно и нужно гонять в CI на каждый
коммит.
"""

from __future__ import annotations

from tools.bench_run import CaseResult, summarize


def _result(**overrides: object) -> CaseResult:
    base: dict[str, object] = dict(
        model="gpt-5.6-sol",
        case_id="belgrade-1/p09.jpg",
        zone="hot_kitchen",
        expected_code="CLN05",
        expected_level="D1",
        got_code="CLN05",
        got_level="D1",
        got_zone="hot_kitchen",
        code_correct=True,
        exact_correct=True,
        confidence=0.9,
        needs_human=False,
        usage={"input": 100, "output": 20},
        error="",
    )
    base.update(overrides)
    return CaseResult(**base)  # type: ignore[arg-type]


def test_точность_по_коду_и_по_коду_с_классом() -> None:
    results = [
        _result(exact_correct=True, code_correct=True),
        _result(exact_correct=False, code_correct=True, got_level="D2"),
        _result(exact_correct=False, code_correct=False, got_code="PRD05"),
        _result(exact_correct=False, code_correct=False, got_code=None, got_level=None),
    ]

    summary = summarize(results)[0]

    assert summary.cases == 4
    assert summary.code_accuracy == 0.5  # 2 из 4
    assert summary.exact_accuracy == 0.25  # 1 из 4


def test_модели_считаются_раздельно_в_порядке_появления() -> None:
    results = [
        _result(model="gpt-5.6-sol", exact_correct=True),
        _result(model="gpt-4o-mini", exact_correct=False),
        _result(model="gpt-5.6-sol", exact_correct=True),
    ]

    summary = summarize(results)

    assert [s.model for s in summary] == ["gpt-5.6-sol", "gpt-4o-mini"]
    sol = next(s for s in summary if s.model == "gpt-5.6-sol")
    mini = next(s for s in summary if s.model == "gpt-4o-mini")
    assert sol.cases == 2
    assert sol.exact_accuracy == 1.0
    assert mini.exact_accuracy == 0.0


def test_ошибки_не_путаются_с_промахами_но_считаются_отдельно() -> None:
    results = [
        _result(exact_correct=True, code_correct=True),
        _result(
            exact_correct=False,
            code_correct=False,
            got_code=None,
            got_level=None,
            error="Модель не ответила: timeout",
        ),
    ]

    summary = summarize(results)[0]

    assert summary.errors == 1
    assert summary.cases == 2
    assert summary.exact_accuracy == 0.5


def test_средние_токены_считаются_только_по_успешным_вызовам() -> None:
    results = [
        _result(usage={"input": 100, "output": 10}),
        _result(usage={"input": 200, "output": 30}),
        _result(usage={}, error="сбой", got_code=None, got_level=None),
    ]

    summary = summarize(results)[0]

    assert summary.input_tokens_avg == 150
    assert summary.output_tokens_avg == 20


def test_пустой_список_не_падает() -> None:
    assert summarize([]) == []
