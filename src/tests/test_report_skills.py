import unittest
from pathlib import Path


class LatexSkillTests(unittest.TestCase):
    def test_requires_evidence_proportional_reports_and_optional_charts(self):
        skill = (
            Path(__file__).resolve().parents[1] / "app" / "skills" / "latex_skill.md"
        ).read_text(encoding="utf-8")
        normalized_skill = " ".join(skill.split())

        self.assertIn("proportional to the available evidence", normalized_skill)
        self.assertIn("Charts are optional, never required", normalized_skill)
        self.assertIn("directly supported finding", normalized_skill)
        self.assertIn("valid for a report to contain no charts", normalized_skill)

    def test_report_skills_exclude_chinese_and_cjk_support(self):
        skills_dir = Path(__file__).resolve().parents[1] / "app" / "skills"
        latex_skill = (skills_dir / "latex_skill.md").read_text(encoding="utf-8")
        ppt_skill = (skills_dir / "ppt_skill.md").read_text(encoding="utf-8")

        self.assertNotIn("Chinese", latex_skill)
        self.assertNotIn("CJK", latex_skill)
        self.assertNotIn("xeCJK", latex_skill)
        self.assertNotIn("Chinese", ppt_skill)
        self.assertNotIn("CJK", ppt_skill)


if __name__ == "__main__":
    unittest.main()
