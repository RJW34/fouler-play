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
    assert result.metadata["llm_authority"] == "advisory_rerank_only"
    assert result.metadata["truth_source"] == "engine_candidate_list"
    assert result.metadata["mechanics_claims_allowed"] is False


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


def test_run_hybrid_rerank_rejects_llm_choice_outside_showdown_request(monkeypatch):
    class FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return '{"choices":[{"message":{"content":"{\\"choice_index\\":1,\\"reason\\":\\"pick illegal coverage\\"}"}}]}'

    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(hybrid_policy.aiohttp, "ClientSession", FakeSession)
    trace = {
        "decision_mode": "eval",
        "eval_scores_raw": {
            "earthquake": 1.0,
            "recover": 0.99,
        },
        "legalOptions": {
            "source": "showdown-request",
            "requestHash": "a" * 64,
            "candidateSetBounded": True,
            "legalMoves": [{"id": "recover"}],
            "legalSwitches": [],
        },
    }

    result = asyncio.run(
        run_hybrid_rerank(
            battle=object(),
            engine_choice="recover",
            trace=trace,
            api_key="dummy",
            model="gpt-4.1-mini",
            api_base="https://api.openai.com/v1",
            timeout_sec=1.0,
            top_k=3,
        )
    )

    assert result.decision is None
    assert result.metadata["status"] == "blocked"
    assert result.metadata["reason"] == "candidate_not_in_showdown_request"
    assert result.metadata["selected_decision"] == "earthquake"


def test_run_hybrid_rerank_blocks_when_showdown_request_legality_missing(monkeypatch):
    class FakeSession:
        def __init__(self, *args, **kwargs):
            raise AssertionError("hybrid rerank must not call the LLM without request-backed legal options")

    monkeypatch.setattr(hybrid_policy.aiohttp, "ClientSession", FakeSession)
    trace = {
        "decision_mode": "eval",
        "eval_scores_raw": {
            "earthquake": 1.0,
            "recover": 0.99,
        },
    }

    result = asyncio.run(
        run_hybrid_rerank(
            battle=object(),
            engine_choice="recover",
            trace=trace,
            api_key="dummy",
            model="gpt-4.1-mini",
            api_base="https://api.openai.com/v1",
            timeout_sec=1.0,
            top_k=3,
        )
    )

    assert result.decision is None
    assert result.metadata["status"] == "blocked"
    assert result.metadata["reason"] == "missing_showdown_request_legal_options"
    assert result.metadata["truth_source"] == "showdown_request_legal_options"
