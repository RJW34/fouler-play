import unittest

from replay_analysis.turn_review import TurnReviewer


SAMPLE_LOG = """
|player|p1|Opponent|elesa|1100
|player|p2|ALL CHUNG|102|1050
|start
|switch|p1a: Ting-Lu|Ting-Lu|100/100
|switch|p2a: Gliscor|Gliscor|100/100
|turn|1
|move|p2a: Gliscor|Spikes|p1a: Ting-Lu
|move|p1a: Ting-Lu|Stealth Rock|p2a: Gliscor
|-sidestart|p1: Opponent|Spikes
|turn|2
|move|p1a: Ting-Lu|Ruination|p2a: Gliscor
|-damage|p2a: Gliscor|50/100
|move|p2a: Gliscor|Earthquake|p1a: Ting-Lu
|-damage|p1a: Ting-Lu|62/100
|turn|3
|move|p1a: Ting-Lu|Earthquake|p2a: Gliscor
|-damage|p2a: Gliscor|0 fnt
|faint|p2a: Gliscor
""".strip()


class TestTurnReviewer(unittest.TestCase):
    def setUp(self):
        self.reviewer = TurnReviewer(bot_username="ALL CHUNG")
        self.replay_data = {"log": SAMPLE_LOG}
        self.url = "https://replay.pokemonshowdown.com/gen9ou-test"

    def test_extract_full_turns_starts_at_turn_one(self):
        turns = self.reviewer.extract_full_turns(self.replay_data, self.url)
        self.assertGreaterEqual(len(turns), 3)
        self.assertEqual(turns[0].turn_number, 1)
        self.assertEqual(turns[0].bot_active.lower(), "gliscor")
        self.assertEqual(turns[0].bot_choice, "spikes")
        self.assertIn("lead turn", turns[0].why_critical.lower())
        self.assertIn("lead matchup:", turns[0].why_critical.lower())
        self.assertTrue(turns[0].lead_matchup.lower().startswith("lead matchup:"))

    def test_detects_bot_side_when_bot_is_p2(self):
        turns = self.reviewer.extract_full_turns(self.replay_data, self.url)
        turn_two = next(t for t in turns if t.turn_number == 2)
        self.assertEqual(turn_two.bot_choice, "earthquake")
        self.assertAlmostEqual(turn_two.bot_hp_percent, 50.0, places=1)
        self.assertAlmostEqual(turn_two.opp_hp_percent, 62.0, places=1)

    def test_critical_turns_always_include_turn_one(self):
        critical = self.reviewer.extract_critical_turns(self.replay_data, self.url)
        self.assertGreaterEqual(len(critical), 1)
        self.assertEqual(critical[0].turn_number, 1)
        self.assertLessEqual(len(critical), 3)


if __name__ == "__main__":
    unittest.main()
