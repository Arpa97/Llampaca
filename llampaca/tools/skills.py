"""
Skills tool: allows the agent to read full instructions from installed Markdown skills.
"""

from typing import Optional
from pathlib import Path

from llampaca import skills
from llampaca.tools.registry import ToolRegistry


def register_skills_tools(registry: ToolRegistry, skills_dir: Optional[Path] = None) -> None:
    """Register the read_skill_page tool on the given ToolRegistry."""

    def read_skill_page(name: str) -> str:
        """
        Read the full Markdown content of an installed skill instruction page.

        Args:
            name: The slug or name of the skill page to read.
        """
        try:
            return skills.read_skill(name, skills_dir=skills_dir)
        except FileNotFoundError:
            available = [s["slug"] for s in skills.list_skills(skills_dir=skills_dir)]
            if available:
                avail_str = ", ".join(f"'{s}'" for s in available)
                return f"Skill '{name}' not found. Available skills: {avail_str}."
            return f"Skill '{name}' not found. No skills currently installed."
        except Exception as e:
            return f"Error reading skill: {e}"

    registry.register(read_skill_page, requires_confirmation=False)
