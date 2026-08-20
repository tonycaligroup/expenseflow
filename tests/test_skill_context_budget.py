from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SkillContextBudgetTests(unittest.TestCase):
    def test_always_loaded_skill_stays_small(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertLessEqual(len(skill), 10_000)
        self.assertLessEqual(len(skill.split()), 1_600)

    def test_detailed_workflows_are_available_on_demand(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")

        for reference in (
            "organization-and-people.md",
            "expense-and-approval-runtime.md",
            "accounting-exports.md",
        ):
            self.assertIn(reference, skill)
            self.assertTrue((ROOT / "references" / reference).is_file())


if __name__ == "__main__":
    unittest.main()
