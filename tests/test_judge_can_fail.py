"""The deployment judge must be able to say no.

Reconstructed from the live judgment that proved it could not:

    activation fouler-activation-ad6a1a180921bf555c95436d5233fbcf (2026-07-20)
        baseline elo 1510 -> post 1415   (95-point drop against maxEloDrop 50)
        win rate 0.40
        recorded "status": "passed"

Cause: `elo_regressed` required `rd is not None`, where the snapshot's
`glickoDeviation` comes from `latest.get("rprd", latest.get("deviation"))`
(deployment_state.py:321). Neither key is written by the battle pipeline — across
all 273 rows of the live battle_stats.json on 2026-07-20 both are ABSENT, not
merely zero — so that conjunct was False in every judgment ever made and the ELO
limb of the gate was dead code.

`glickoDeviation` exists to SUPPRESS the check while a rating is provisional. An
unknown deviation is not evidence of a stable rating, so it must not be able to
disable the gate outright.
"""

import unittest

from infrastructure.deployment_state import _judgment_outcome

MAX_ELO_DROP = 50.0
MAX_GLICKO_DEVIATION = 100.0


class TestJudgeCanFail(unittest.TestCase):
    def test_live_regression_with_absent_glicko_deviation_is_caught(self):
        """The exact numbers that recorded 'passed' on 2026-07-20."""
        status, detail = _judgment_outcome(
            {"elo": 1510, "winRate": 0.4666},
            {"elo": 1415, "winRate": 0.40},  # no glickoDeviation key, as in live data
            max_elo_drop=MAX_ELO_DROP,
            max_glicko_deviation=MAX_GLICKO_DEVIATION,
        )
        self.assertEqual(
            status, "regressed",
            "a 95-point ELO drop at a 0.40 win rate must be caught even when "
            "glickoDeviation is absent",
        )
        self.assertTrue(detail.get("eloRegressed"))

    def test_provisional_rating_still_suppresses_the_elo_limb(self):
        """A genuinely provisional rating (high rd) must still suppress it."""
        status, detail = _judgment_outcome(
            {"elo": 1510, "winRate": 0.4666},
            {"elo": 1415, "winRate": 0.40, "glickoDeviation": 250.0},
            max_elo_drop=MAX_ELO_DROP,
            max_glicko_deviation=MAX_GLICKO_DEVIATION,
        )
        self.assertFalse(detail.get("eloRegressed"))

    def test_healthy_deployment_still_passes(self):
        """Over-correction guard: a good deployment must not be failed."""
        status, _ = _judgment_outcome(
            {"elo": 1450, "winRate": 0.50},
            {"elo": 1520, "winRate": 0.60},
            max_elo_drop=MAX_ELO_DROP,
            max_glicko_deviation=MAX_GLICKO_DEVIATION,
        )
        self.assertNotEqual(status, "regressed")

    def test_small_drop_within_tolerance_still_passes(self):
        """A 20-point drop is inside maxEloDrop 50 and must not fail."""
        status, detail = _judgment_outcome(
            {"elo": 1450, "winRate": 0.50},
            {"elo": 1430, "winRate": 0.48},
            max_elo_drop=MAX_ELO_DROP,
            max_glicko_deviation=MAX_GLICKO_DEVIATION,
        )
        self.assertFalse(detail.get("eloRegressed"))


if __name__ == "__main__":
    unittest.main()
