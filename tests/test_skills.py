import tempfile
import unittest
from pathlib import Path

from llampaca import skills
from llampaca.tools.skills import register_skills_tools
from llampaca.tools.registry import ToolRegistry


class TestSkills(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.skills_dir = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_slugify(self):
        self.assertEqual(skills.slugify("Python Expert.md"), "python_expert")
        self.assertEqual(skills.slugify("Git Workflow"), "git_workflow")

    def test_write_and_read_skill(self):
        content = "# Python Skill\n\nIstruzioni per Python."
        slug = skills.write_skill("python_expert", content, skills_dir=self.skills_dir)
        self.assertEqual(slug, "python_expert")

        read_content = skills.read_skill("python_expert", skills_dir=self.skills_dir)
        self.assertEqual(read_content, content)

    def test_yaml_frontmatter_metadata(self):
        content = """---
name: Clean Code
description: Istruzioni per scrivere codice pulito e modulare.
---

# Clean Code Guidelines
- Funzioni brevi
- Nomi espressivi
"""
        skills.write_skill("clean_code", content, skills_dir=self.skills_dir)
        skill_list = skills.list_skills(skills_dir=self.skills_dir)
        self.assertEqual(len(skill_list), 1)
        self.assertEqual(skill_list[0]["name"], "Clean Code")
        self.assertEqual(skill_list[0]["description"], "Istruzioni per scrivere codice pulito e modulare.")

    def test_delete_skill(self):
        skills.write_skill("temp_skill", "# Temp", skills_dir=self.skills_dir)
        self.assertTrue(skills.delete_skill("temp_skill", skills_dir=self.skills_dir))
        self.assertEqual(len(skills.list_skills(skills_dir=self.skills_dir)), 0)

    def test_render_skills_index(self):
        skills.write_skill("git_helper", "# Git Helper\nComandi utili per Git.", skills_dir=self.skills_dir)
        index_str = skills.render_skills_index(skills_dir=self.skills_dir)
        self.assertIn("Available Markdown Skills & Instructions:", index_str)
        self.assertIn("git_helper", index_str)

    def test_read_skill_page_tool(self):
        skills.write_skill("code_reviewer", "# Code Reviewer", skills_dir=self.skills_dir)
        registry = ToolRegistry()
        register_skills_tools(registry, skills_dir=self.skills_dir)

        tool = registry.get("read_skill_page")
        self.assertIsNotNone(tool)

        res = tool.func("code_reviewer")
        self.assertEqual(res, "# Code Reviewer")


if __name__ == "__main__":
    unittest.main()
