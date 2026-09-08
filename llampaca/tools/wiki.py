"""
Personal wiki agent tools: read_wiki_page and update_wiki_page.

These are the model-facing side of llampaca/wiki.py. Unlike
search_documents (registered per-conversation, only when something is
indexed), the wiki tools are part of build_default_registry(): the wiki is
global memory, relevant to every session. Only two tools on purpose —
small local models call tools more reliably the fewer they see — and no
delete tool at all: removing memories is a user decision, done by deleting
files from the wiki directory.

The "when should the model write?" policy lives in update_wiki_page's
docstring (which becomes the tool description the model reads), not in the
system prompt: it belongs next to the capability it governs, and it only
costs prompt space when tools are enabled anyway. The actual write is
additionally gated behind user confirmation (requires_confirmation), so a
trigger-happy model costs the user one keystroke, never a polluted wiki.

Tools return strings in every case — including failures — so the model can
read the error and adapt (same contract as every other tool module).
"""

from pathlib import Path
from typing import Optional

from llampaca import wiki


def register_wiki_tools(registry, wiki_dir: Optional[Path] = None) -> None:
    """
    Register the wiki tools on an existing registry.

    Args:
        registry: The session's ToolRegistry.
        wiki_dir: Wiki directory override for tests; None uses the app
            default (~/.llampaca/wiki).
    """

    def read_wiki_page(name: str) -> str:
        """
        Read one page of your persistent wiki about the user. The wiki
        survives across conversations: consult it before answering
        questions about the user's preferences, facts, or projects. The
        list of existing pages is in your system prompt.

        Args:
            name: Name of the page to read, e.g. 'user-preferences'.
        """
        try:
            return wiki.read_page(name, wiki_dir)
        except FileNotFoundError:
            # Include the real names so a model that misremembered a page
            # (or hallucinated one) can immediately self-correct.
            names = ", ".join(n for n, _ in wiki.list_pages(wiki_dir))
            existing = f" Existing pages: {names}." if names else (
                " The wiki has no pages yet."
            )
            return f"Error: wiki page '{name}' does not exist.{existing}"
        except ValueError as e:  # invalid name (slugify refused it)
            return f"Error: {e}"

    def update_wiki_page(name: str, content: str) -> str:
        """
        Create or overwrite a page of your persistent wiki about the user.
        Use it ONLY for durable facts worth remembering in future
        conversations: the user's preferences, stable facts about them,
        long-running projects. Never store details that only matter to the
        current conversation, and never store secrets (passwords, keys).
        Overwrites the WHOLE page: to add to an existing page, read it
        first and include its still-valid content in the new version.

        Args:
            name: Name of the page to write, e.g. 'user-preferences'. Use
                an existing page when the fact fits one (see the page list
                in your system prompt); a new name creates a new page.
            content: The full new markdown content of the page. Start with
                a '# Title' line: it becomes the page's description in the
                index.
        """
        try:
            written = wiki.write_page(name, content, wiki_dir)
        except ValueError as e:  # invalid name, empty or oversized content
            return f"Error: {e}"
        # Echo the slugified name: it is the name the model must use to
        # read the page back, and it may differ from what it asked for
        # (e.g. 'User Preferences' -> 'user-preferences').
        return f"Wiki page '{written}' saved."

    registry.register(read_wiki_page, requires_confirmation=False)
    # Writing persistent memory is gated like every other write in the
    # codebase: the user sees the exact page name and content and approves.
    registry.register(update_wiki_page, requires_confirmation=True)
