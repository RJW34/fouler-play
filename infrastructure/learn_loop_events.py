"""Discord events for the self-learning loop and for engine changes.

The owner asked for three things this module provides:

    "maybe another that says 'self-learning process starting/finishing now' and
     an explanation for the changes made to the engine would also be good, too,
     because it would help not just me, but agents to understand if the project
     is working as intended or not."

The reason matters more than the format. These messages exist so that a reader --
human or agent -- can tell whether the project is working as intended. So each
one has to answer "what changed, and does it matter?". A cycle that changed
nothing says so in one line and stops; it does not get a status post for having
run.

WHY THE ENGINE-CHANGE SHAPE IS NOT NEW
An engine change is reported as hypothesis -> change -> predicted effect ->
measured result -> kept/reverted. Those are not invented fields: they are the
ones already carried by `replay_analysis/hypothesis_ledger` records
(`predictedChange`, `recommendation`, `evidence`, and the
`open|implemented|deployed|measured|kept|reverted` lifecycle in its docstring)
joined to the verdict vocabulary the outcome-pairing work in devstream-spine
uses (`confirmed|refuted|pending|unmeasurable`, per `docs/OUTCOME_PAIRING.md`).
Reusing both is deliberate: a second, parallel record of "what did we change and
did it work" is exactly how a system ends up with two answers to that question.

UNMEASURED IS NOT SUCCESS.
`measured` defaults to the explicit string "not yet measured" rather than being
omitted, and the verdict defaults to "pending". A change whose effect nobody has
measured must never read as a change that worked -- that is the failure mode the
outcome-pairing discipline exists to prevent.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

LEARN_LOOP_EVENTS_ENABLED = str(
    os.getenv("FOULER_LEARN_LOOP_EVENTS", "1")
).strip().lower() not in {"0", "false", "no", "off"}

# Channel routes to the fouler-play category (see DEKU_CATEGORY_BY_CHANNEL).
LEARN_LOOP_CHANNEL = "project"

# Outcomes that mean the improve agent did not touch the engine. These get no
# post at all: "the loop ran and changed nothing" is not news, and posting it
# every cycle is precisely the repetitive noise the owner asked us to remove.
QUIET_OUTCOMES = {"no_change", "blocked"}

VERDICTS = ("confirmed", "refuted", "pending", "unmeasurable")


def _queue(event_type: str, content: str, *, dedup_key: str | None = None) -> str | None:
    """Queue an event, never letting reporting failure break the learning loop."""
    if not LEARN_LOOP_EVENTS_ENABLED:
        return None
    try:
        from infrastructure.event_queue_lib import queue_event

        return queue_event(
            event_type,
            LEARN_LOOP_CHANNEL,
            content,
            dedup_key=dedup_key,
        )
    except Exception as exc:  # noqa: BLE001 - reporting must not break the loop
        # Logged, not swallowed. A silent except here is how the hypothesis
        # ledger emit lost writes without anyone noticing.
        logger.warning("Could not queue %s event: %s", event_type, exc)
        return None


def _payload(event_class: str, headline: str, what: str, why: str, proof: str, remaining: str) -> str:
    from infrastructure.discord_reporting import build_contract_payload

    return build_contract_payload(
        event_class,
        headline,
        what,
        why,
        proof,
        remaining,
        source="infrastructure.learn_loop_events",
    )


def emit_learn_cycle_started(
    *,
    cycle_index: int | None = None,
    target_issue: str | None = None,
    target_file: str | None = None,
) -> str | None:
    """Announce that the self-learning loop has begun a cycle."""
    label = f"cycle {cycle_index}" if cycle_index is not None else "a cycle"
    issue = str(target_issue or "").strip()
    headline = f"Self-learning cycle starting ({label})"
    what = f"The improve loop began {label}."
    if issue:
        what += f" It is working on: {issue}."
    why = (
        "Marks the start of an engine-modifying window. A cycle that starts and "
        "never reports a finish is a hung loop, which is otherwise invisible."
    )
    proof_bits = [f"cycle={cycle_index}" if cycle_index is not None else "cycle=unknown"]
    if issue:
        proof_bits.append(f"issue={issue}")
    if target_file:
        proof_bits.append(f"target_file={target_file}")
    return _queue(
        "learn_cycle_started",
        _payload(
            "PROGRESSION",
            headline,
            what,
            why,
            "; ".join(proof_bits),
            "Wait for the matching finish event before drawing conclusions.",
        ),
        dedup_key=f"fouler-play:learn-cycle-started:{cycle_index}",
    )


def emit_learn_cycle_finished(
    *,
    cycle_index: int | None = None,
    outcome: str,
    issue: str | None = None,
    target_file: str | None = None,
    duration_sec: float | None = None,
    detail: dict[str, Any] | None = None,
) -> str | None:
    """Announce that a self-learning cycle ended, and what it did."""
    label = f"cycle {cycle_index}" if cycle_index is not None else "a cycle"
    outcome_clean = str(outcome or "unknown").strip().lower()
    headline = f"Self-learning cycle finished ({label}): {outcome_clean}"
    what = f"The improve loop finished {label} with outcome '{outcome_clean}'."
    if issue:
        what += f" Issue worked: {issue}."
    if outcome_clean == "accepted":
        why = (
            "The engine changed. The paired engine-change event carries the "
            "hypothesis and the predicted effect; the result is not measured yet."
        )
    elif outcome_clean == "rejected":
        why = (
            "A candidate change was produced and REJECTED by the evaluation gate, "
            "so the engine is unchanged. The gate doing its job is the signal here."
        )
    else:
        why = (
            "The engine is unchanged. Recorded so a run of cycles that change "
            "nothing is visible as such rather than looking like silence."
        )
    proof_bits = [f"outcome={outcome_clean}"]
    if cycle_index is not None:
        proof_bits.append(f"cycle={cycle_index}")
    if target_file:
        proof_bits.append(f"target_file={target_file}")
    if duration_sec is not None:
        proof_bits.append(f"duration_sec={round(float(duration_sec), 1)}")
    if isinstance(detail, dict) and detail.get("reason"):
        proof_bits.append(f"reason={detail['reason']}")
    return _queue(
        "learn_cycle_finished",
        _payload(
            "PROGRESSION",
            headline,
            what,
            why,
            "; ".join(proof_bits),
            "No action required unless the outcome is unexpected.",
        ),
        dedup_key=f"fouler-play:learn-cycle-finished:{cycle_index}:{outcome_clean}",
    )


def emit_engine_change_explained(
    *,
    change_id: str | None = None,
    hypothesis: str,
    change: str,
    predicted_effect: str,
    measured_result: str | None = None,
    verdict: str = "pending",
    target_file: str | None = None,
    evidence: list[str] | None = None,
) -> str | None:
    """Explain one engine change: hypothesis, change, prediction, result, verdict.

    Emitted only for changes that actually landed. `measured_result` and
    `verdict` are expected to be unmeasured/pending at land time -- that is the
    honest state, and it is stated rather than hidden.
    """
    verdict_clean = str(verdict or "pending").strip().lower()
    if verdict_clean not in VERDICTS:
        verdict_clean = "pending"
    measured = str(measured_result or "").strip() or "not yet measured"

    disposition = {
        "confirmed": "kept",
        "refuted": "reverted",
        "pending": "kept pending measurement",
        "unmeasurable": "kept, but this claim is not resolvable against the current baseline",
    }[verdict_clean]

    headline = f"Engine change: {str(change)[:90]}"
    what = (
        f"Hypothesis: {hypothesis}\n"
        f"Change: {change}\n"
        f"Predicted effect: {predicted_effect}\n"
        f"Measured result: {measured}\n"
        f"Verdict: {verdict_clean} -> {disposition}"
    )
    why = (
        "Lets a reader decide whether the project is working as intended without "
        "reading the diff: a change whose predicted effect never shows up in the "
        "measured result is a change that should come back out."
    )
    proof_bits = [f"verdict={verdict_clean}"]
    if change_id:
        proof_bits.append(f"change_id={change_id}")
    if target_file:
        proof_bits.append(f"target_file={target_file}")
    for item in (evidence or [])[:4]:
        proof_bits.append(str(item))
    remaining = (
        "Measure the predicted effect and re-report the verdict."
        if verdict_clean == "pending"
        else "None; the verdict is decided."
    )
    return _queue(
        "engine_change_explained",
        _payload(
            "CODE_FIX",
            headline,
            what,
            why,
            "; ".join(proof_bits),
            remaining,
        ),
        dedup_key=f"fouler-play:engine-change:{change_id or change}"[:180],
    )
