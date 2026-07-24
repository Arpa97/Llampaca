"""
Markdown Skills manager: reusable Markdown instructions, prompts, and domain workflows.

Skills are stored as plain Markdown (.md) files in ~/.llampaca/skills/
(config.SKILLS_DIR). Each skill file can be:
1. A plain markdown file starting with a header (# Skill Title)
2. A markdown file with YAML frontmatter (e.g. --- \n name: skill-name \n description: ... \n ---)

This module handles listing, reading, writing, deleting, and rendering skills
for injection into the agent system prompt.
"""

import re
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

from llampaca.config import CHARS_PER_TOKEN, SKILLS_DIR

MAX_SKILL_TOKENS = 3000
MAX_SKILL_CHARS = MAX_SKILL_TOKENS * CHARS_PER_TOKEN
MAX_INDEX_SKILLS = 40
INDEX_DESCRIPTION_CHARS = 100

_SLUG_ALLOWED = re.compile(r"[a-z0-9_]+")


def slugify(name: str) -> str:
    """Normalize a skill name into a safe filename stem."""
    stem = name.strip().lower()
    if stem.endswith(".md"):
        stem = stem[:-3]
    parts = _SLUG_ALLOWED.findall(stem)
    if not parts:
        raise ValueError(
            f"Nome skill non valido {name!r}: usa solo lettere, numeri e underscore."
        )
    return "_".join(parts)


def skill_path(name: str, skills_dir: Optional[Path] = None) -> Path:
    """The on-disk path for a skill file (slugified, '.md' appended)."""
    return (skills_dir or SKILLS_DIR) / f"{slugify(name)}.md"


def _parse_skill_metadata(content: str, fallback_stem: str) -> Dict[str, str]:
    """
    Extract title/name and description from YAML frontmatter or Markdown headers.
    """
    name = fallback_stem
    description = ""

    # Check for YAML frontmatter
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            yaml_text = parts[1]
            for line in yaml_text.splitlines():
                if ":" in line:
                    key, val = line.split(":", 1)
                    key = key.strip().lower()
                    val = val.strip().strip("'\"")
                    if key in ("name", "title") and val:
                        name = val
                    elif key == "description" and val:
                        description = val

    # Fallback to header/first lines if description or name not in YAML
    lines = [ln.strip() for ln in content.splitlines() if ln.strip() and not ln.startswith("---")]
    for line in lines:
        if line.startswith("#"):
            header_text = line.lstrip("#").strip()
            if name == fallback_stem and header_text:
                name = header_text
        elif not description:
            description = line[:INDEX_DESCRIPTION_CHARS]
            break

    return {
        "name": name,
        "description": description or "Istruzioni avanzate in formato Markdown."
    }


def list_skills(skills_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """
    All installed skills as a list of metadata dictionaries.
    """
    directory = skills_dir or SKILLS_DIR
    if not directory.is_dir():
        return []

    skills: List[Dict[str, Any]] = []
    for path in sorted(directory.glob("*.md")):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            meta = _parse_skill_metadata(content, path.stem)
            skills.append({
                "slug": path.stem,
                "name": meta["name"],
                "description": meta["description"],
                "file_name": path.name,
                "file_path": str(path),
                "content": content
            })
        except OSError:
            continue
    return skills


def read_skill(name: str, skills_dir: Optional[Path] = None) -> str:
    """
    The full Markdown content of a skill.
    """
    path = skill_path(name, skills_dir)
    if not path.is_file():
        # Try finding by matching slug
        directory = skills_dir or SKILLS_DIR
        for p in directory.glob("*.md"):
            if p.stem == slugify(name):
                return p.read_text(encoding="utf-8", errors="replace")
        raise FileNotFoundError(slugify(name))
    return path.read_text(encoding="utf-8", errors="replace")


def write_skill(name: str, content: str, skills_dir: Optional[Path] = None) -> str:
    """
    Create or overwrite a skill Markdown file.

    The on-disk slug is derived from the name declared *inside* the file
    (YAML frontmatter `name:`/`title:` or the first Markdown header) when
    present, so the filename matches the skill's declared identity and the
    slug advertised in the system-prompt index. The `name` argument is only
    used as a fallback when the content declares no name of its own.
    """
    if not content.strip():
        raise ValueError("Il contenuto della skill non può essere vuoto.")
    if len(content) > MAX_SKILL_CHARS:
        raise ValueError(
            f"Il contenuto della skill supera il limite massimo di {MAX_SKILL_CHARS} caratteri."
        )

    # Prefer the name declared in the file so file and content stay aligned;
    # fall back to the provided argument (already validated by slugify).
    meta = _parse_skill_metadata(content, fallback_stem=slugify(name))
    path = skill_path(meta["name"], skills_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path.stem


def delete_skill(name: str, skills_dir: Optional[Path] = None) -> bool:
    """
    Delete a skill file by name/slug.
    """
    directory = skills_dir or SKILLS_DIR
    slug = slugify(name)
    path = directory / f"{slug}.md"
    if path.exists():
        path.unlink()
        return True

    # Search by glob
    for p in directory.glob("*.md"):
        if p.stem == slug:
            p.unlink()
            return True
    return False


def render_skills_index(skills_dir: Optional[Path] = None) -> str:
    """
    Format the skills section for inclusion in the agent's system prompt.
    """
    skills = list_skills(skills_dir)
    if not skills:
        return ""

    lines = ["Available Markdown Skills & Instructions:"]
    for s in skills[:MAX_INDEX_SKILLS]:
        lines.append(f"- {s['slug']}: {s['description']} (Read full instructions using tool read_skill_page('{s['slug']}'))")

    return "\n".join(lines)
