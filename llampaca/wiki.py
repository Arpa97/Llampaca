"""
Personal wiki core: the model's (and the user's) long-term memory.

The wiki is a flat directory of plain markdown pages in ~/.llampaca/wiki/
(config.WIKI_DIR) — app data, deliberately OUTSIDE the launch workspace:
it is the user's memory, shared by every project and every session. Pages
are human-readable and human-editable on purpose: with small local models,
being able to inspect and correct what the model "learned" (with any text
editor) is worth more than any opaque vector store.

This module is pure file logic — no server, no database, no agent
knowledge. The pipeline around it:

    cli.py           ->  render_index()      (injected in the system prompt)
    tools/wiki.py    ->  read_page() / write_page() / list_pages()
    the user         ->  edits the .md files directly, if they want

No RAG on purpose: a personal wiki is a few dozen short titled pages, so a
title index (a handful of prompt tokens) plus deterministic whole-page
reads beats fuzzy retrieval for a small model. If the wiki ever outgrows
this, the embedding pipeline in rag.py / engine/embedding.py is ready.

Every function takes an optional wiki_dir override so tests can point the
whole module at a tmp directory (same pattern as db_path in engine/db.py).
"""

import re
from pathlib import Path
from typing import List, Optional, Tuple

from llampaca.config import CHARS_PER_TOKEN, WIKI_DIR

# Size cap for one page, in (estimated) tokens. A page must always be
# readable IN FULL next to the system prompt and some conversation, even
# on a below-default context window (sized to fit comfortably at 4096,
# half the default 8192) — whole-page reads are the point of the no-RAG
# design, so a page that cannot fit would defeat it. Oversized writes are
# rejected with a message telling the model to split the page.
MAX_PAGE_TOKENS = 1500
MAX_PAGE_CHARS = MAX_PAGE_TOKENS * CHARS_PER_TOKEN

# Cap on the number of pages listed by render_index(). The index rides in
# the system prompt of EVERY request, so it must stay cheap even if the
# wiki grows unexpectedly large; beyond the cap the index says how many
# pages were omitted (they stay reachable by name via read_wiki_page).
MAX_INDEX_PAGES = 30

# How much of a page's first line is shown as its description in the index.
INDEX_DESCRIPTION_CHARS = 80

# What slugify() keeps: runs of anything else collapse into one hyphen.
_SLUG_ALLOWED = re.compile(r"[a-z0-9]+")


def slugify(name: str) -> str:
    """
    Normalize a model- or user-provided page name into a safe filename stem.

    Lowercases, strips an optional ".md" extension, and collapses every
    run of characters outside [a-z0-9] into a single hyphen. Because the
    output alphabet is only [a-z0-9-], path traversal is impossible by
    construction — "../../etc/passwd" degrades to "etc-passwd", a harmless
    name inside the wiki directory.

    Raises ValueError when nothing usable remains (empty name, pure
    punctuation): page names come from the model, and a loud error it can
    read beats silently writing to a garbage filename.
    """
    stem = name.strip().lower()
    if stem.endswith(".md"):
        stem = stem[:-3]
    parts = _SLUG_ALLOWED.findall(stem)
    if not parts:
        raise ValueError(
            f"Invalid wiki page name {name!r}: use letters and numbers, "
            "e.g. 'user-preferences'."
        )
    return "-".join(parts)


def page_path(name: str, wiki_dir: Optional[Path] = None) -> Path:
    """The on-disk path for a page name (slugified, '.md' appended)."""
    return (wiki_dir or WIKI_DIR) / f"{slugify(name)}.md"


def list_pages(wiki_dir: Optional[Path] = None) -> List[Tuple[str, str]]:
    """
    All wiki pages as (name, description) tuples, sorted by name.

    The description is the page's first non-empty line — markdown heading
    markers stripped, truncated to INDEX_DESCRIPTION_CHARS — so a page
    that opens with "# Preferenze dell'utente" self-documents in the
    index. A missing wiki directory just means an empty wiki.
    """
    directory = wiki_dir or WIKI_DIR
    if not directory.is_dir():
        return []

    pages: List[Tuple[str, str]] = []
    for path in sorted(directory.glob("*.md")):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            # An unreadable page (permissions, broken symlink) must not
            # take the whole index down with it.
            continue
        pages.append((path.stem, _first_line_description(content)))
    return pages


def read_page(name: str, wiki_dir: Optional[Path] = None) -> str:
    """
    The full text of a page. Raises FileNotFoundError when the page does
    not exist (callers turn it into a model-readable message that includes
    the names that DO exist).
    """
    path = page_path(name, wiki_dir)
    if not path.is_file():
        raise FileNotFoundError(slugify(name))
    return path.read_text(encoding="utf-8", errors="replace")


def write_page(name: str, content: str, wiki_dir: Optional[Path] = None) -> str:
    """
    Create or overwrite a page with the given full content.

    Returns the slugified page name actually written (it may differ from
    the requested name — the caller reports it so the model learns the
    real name). Raises ValueError for empty content (deleting a page is a
    user action, done with a file manager — the model only writes) and for
    content over MAX_PAGE_CHARS (the page must stay whole-readable, see
    the cap's comment).
    """
    if not content.strip():
        raise ValueError(
            "Refusing to write an empty page. To remove a page, the user "
            "can delete the file from the wiki directory."
        )
    if len(content) > MAX_PAGE_CHARS:
        raise ValueError(
            f"Page content is too long ({len(content)} characters, limit "
            f"{MAX_PAGE_CHARS}). Split the content into smaller pages."
        )

    path = page_path(name, wiki_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path.stem


def render_index(wiki_dir: Optional[Path] = None) -> str:
    """
    The wiki section of the system prompt: what the wiki is, plus one
    "- name: description" line per page (capped at MAX_INDEX_PAGES).

    Always non-empty: when the wiki has no pages the section says so
    explicitly — the model must know the wiki EXISTS even when empty,
    otherwise it can never decide to create the first page.
    """
    pages = list_pages(wiki_dir)

    header = (
        "You have a personal wiki: persistent notes about the user that "
        "survive across conversations. Read a page with read_wiki_page "
        "before answering questions the wiki likely covers (the user's "
        "preferences, facts about them, their ongoing projects)."
    )
    if not pages:
        return (
            header + " The wiki is currently empty; create the first page "
            "with update_wiki_page when you learn something durable about "
            "the user."
        )

    lines = [
        f"- {name}: {description}" if description else f"- {name}"
        for name, description in pages[:MAX_INDEX_PAGES]
    ]
    omitted = len(pages) - MAX_INDEX_PAGES
    if omitted > 0:
        lines.append(
            f"... and {omitted} more pages (readable by name with "
            "read_wiki_page)."
        )
    return header + " Current pages:\n" + "\n".join(lines)


def _first_line_description(content: str) -> str:
    """
    A page's index description: its first non-empty line, leading markdown
    heading markers ("#", "##", ...) stripped, truncated with an ellipsis.
    """
    for line in content.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            if len(stripped) > INDEX_DESCRIPTION_CHARS:
                stripped = stripped[:INDEX_DESCRIPTION_CHARS - 1] + "…"
            return stripped
    return ""
