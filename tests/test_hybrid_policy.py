import asyncio
import time

import fp.hybrid_policy as hybrid_policy
from fp.hybrid_policy import (
    _extract_json_dict,
    build_rerank_candidates,
    run_hybrid_rerank,
)


def test_extract_json_dict_handles_code_fence():
    raw = """```json
{"choice_index": 2, "reason": "safe line"}
```"""
    parsed = _extract_json_dict(raw)
    assert parsed is not None
    assert parsed["choice_index"] == 2


def test_build_rerank_candidates_keeps_engine_choice():
    trace = {
        "eval_scores_raw": {
            "move1": 4.0,
            "move2": 3.5,
            "move3": 3.0,
            "move4": 2.5,
        }
    }
    candidates = build_rerank_candidates(
        engine_choice="move4",
        trace=trace,
        top_k=2,
    )
    decisions = [c["decision"] for c in candidates]
    assert "move1" in decisions
    assert "move2" in decisions
    assert "move4" in decisions


def test_run_hybrid_rerank_skips_when_non_eval_mode():
    result = asyncio.run(
        run_hybrid_rerank(
            battle=object(),
            engine_choice="move1",
            trace={"decision_mode": "fallback"},
            api_key="dummy",
            model="gpt-4.1-mini",
            api_base="https://api.openai.com/v1",
            timeout_sec=1.0,
            top_k=3,
        )
    )
    assert result.decision is None
    assert result.metadata["status"] == "skipped"
    assert result.metadata["engine_choice"] == "move1"
    assert isinstance(result.metadata["candidates"], list)


def test_run_hybrid_rerank_skips_when_clear_best_eval():
    trace = {
        "decision_mode": "eval",
        "eval_scores_raw": {
            "move1": 10.0,
            "move2": 1.0,
            "move3": 0.5,
        },
    }
    result = asyncio.run(
        run_hybrid_rerank(
            battle=object(),
            engine_choice="move1",
            trace=trace,
            api_key="dummy",
            model="gpt-4.1-mini",
            api_base="https://api.openai.com/v1",
            timeout_sec=1.0,
            top_k=3,
        )
    )
    assert result.decision is None
    assert result.metadata["status"] == "skipped"
    assert result.metadata["reason"] == "clear_best_engine"


def test_run_hybrid_rerank_skips_when_in_backoff_window():
    old = hybrid_policy._RERANK_RATE_LIMIT_UNTIL
    hybrid_policy._RERANK_RATE_LIMIT_UNTIL = time.time() + 60
    try:
        result = asyncio.run(
            run_hybrid_rerank(
                battle=object(),
                engine_choice="move1",
                trace={
                    "decision_mode": "eval",
                    "eval_scores_raw": {"move1": 1.0, "move2": 0.95},
                },
                api_key="dummy",
                model="gpt-4.1-mini",
                api_base="https://api.openai.com/v1",
                timeout_sec=1.0,
                top_k=3,
            )
        )
        assert result.decision is None
        assert result.metadata["status"] == "skipped"
        assert result.metadata["reason"] == "rate_limited_backoff"
        assert result.metadata["retry_in_sec"] >= 1
    finally:
        hybrid_policy._RERANK_RATE_LIMIT_UNTIL = old
