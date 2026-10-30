"""Dark-channel warning predicates (incident 2026-10-30, code-review findings).

Pins the semantics that are easiest to get wrong: which states count as
mid-cycle, whether all-blank dropout frames count as channel evidence, and
the threshold/reset interplay. stdlib unittest (no pytest in the addon env).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from wm_local_bridge import (  # noqa: E402
    BLANK,
    TERMINAL_STATES,
    should_warn_washer_dark,
    usable_snapshot,
)


class TestShouldWarnWasherDark(unittest.TestCase):
    def test_mid_cycle_dark_warns(self):
        self.assertTrue(should_warn_washer_dark("WM_STATE_RUNNING", 1801, 10, False))

    def test_terminal_state_never_warns(self):
        # a powered-off washer is legitimately silent (PROTOCOL.md)
        for state in TERMINAL_STATES:
            self.assertFalse(should_warn_washer_dark(state, 999999, 10, False))

    def test_pause_and_detecting_are_mid_cycle(self):
        # allowlist miss class (code-review 2026-10-30 finding 2)
        for state in ("WM_STATE_PAUSE", "WM_STATE_DETECTING", "WM_STATE_DRYING"):
            self.assertTrue(should_warn_washer_dark(state, 1801, 10, False))

    def test_no_warn_below_threshold(self):
        self.assertFalse(should_warn_washer_dark("WM_STATE_RUNNING", 1799, 10, False))

    def test_no_warn_when_relay_quiet(self):
        # relay dead: the generic quiet warning covers it instead
        self.assertFalse(should_warn_washer_dark("WM_STATE_RUNNING", 1801, 120, False))

    def test_no_warn_twice(self):
        self.assertFalse(should_warn_washer_dark("WM_STATE_RUNNING", 1801, 10, True))

    def test_missing_state_is_not_terminal(self):
        self.assertTrue(should_warn_washer_dark(None, 1801, 10, False))


class TestUsableSnapshot(unittest.TestCase):
    def test_none_is_not_usable(self):
        self.assertFalse(usable_snapshot(None))

    def test_all_blank_dropout_is_not_usable(self):
        self.assertFalse(usable_snapshot({"State": "-", "Course": "none", "A": ""}))

    def test_real_snapshot_is_usable(self):
        self.assertTrue(usable_snapshot({"State": "WM_STATE_RUNNING", "Course": "Mix"}))

    def test_blank_vocabulary_untouched(self):
        # the predicate must use the same vocabulary as the publish guard
        self.assertEqual(BLANK, {"", "unknown", "unavailable", "none", "None", "-"})


if __name__ == "__main__":
    unittest.main()
