import json
from datetime import datetime
from typing import Dict, List, Any

DEFAULT_SYSTEM_PROMPT = (
    "You are Llampaca, a helpful local AI personal assistant running entirely "
    "on the user's machine. You can use the available tools to generate images, read and write "
    "files in the user's workspace, run shell commands, search the web, and "
    "fetch web pages. "
    "CRITICAL RULE FOR IMAGE GENERATION: Whenever the user asks to generate, create, draw, or render an image, picture, visual, or illustration, you MUST IMMEDIATELY call the 'generate_image' tool on the FIRST turn. Never describe or pretend to generate an image in plain text without calling the 'generate_image' tool! "
    "CRITICAL RULE FOR SKILLS: If the user's request matches any Available Markdown Skill listed below (such as creating/editing Word .docx files using the 'docx' skill, etc.), you MUST call read_skill_page('<skill_slug>') FIRST to read the complete workflow and instructions before calling file or shell tools! "
    "When the user asks about current events or facts that may "
    "have changed since your training, or anything you are unsure of or do "
    "not know, use web_search rather than guessing, and cite what you found. "
    "You cannot see individual characters in text and you cannot do reliable "
    "arithmetic in your head. For anything that must be exact — counting "
    "characters, letters, words or lines, reversing or sorting text, and "
    "arithmetic — do not guess: use run_shell_command to compute the answer, "
    "then report the computed result. "
    "If the user asks about his identity, read the right page with the profile with 'read_wiki_page'. "
    "If the user asks you to update his identity or personal information, update the right page with the profile "
    "using 'update_wiki_page' tool. Do not delete information unless explicitly asked by the user. "
    "Use tools when they help you answer accurately; answer directly when you "
    "don't need them. Be concise."
)

def build_base_system_prompt(base_prompt: str = None) -> str:
    prompt = base_prompt or DEFAULT_SYSTEM_PROMPT
    today = datetime.now().strftime("%A, %d %B %Y")
    year = datetime.now().year
    return (
        f"{prompt} "
        f"Today's date is {today}. The current year is {year}. "
        f"La data di oggi è: {today}. L'anno corrente è {year}. "
        f"When searching the web, ALWAYS include the current year ({year}) in the query "
        f"if the user's question refers to recent or current events."
    )

def tool_instructions(registry: Any) -> str:
    definitions = [d["function"] for d in registry.definitions()]
    return (
        "You have access to the following tools, described as JSON schemas:\n"
        + json.dumps(definitions)
        + "\n\nTo use a tool, reply with ONLY a single JSON object in this "
        'exact format and nothing else:\n{"name": "<tool_name>", '
        '"arguments": {<parameters>}}\n'
        "You will receive the tool result in the next message; then answer "
        "the user (or call another tool). Never invent or assume tool "
        "results: if you need one, emit the JSON and wait."
    )

def build_system_prompt_content(
    base_system_prompt: str,
    tools_enabled: bool,
    native_tools: bool,
    registry: Any
) -> str:
    content = base_system_prompt
    if tools_enabled and not native_tools and registry:
        content += "\n\n" + tool_instructions(registry)
    return content

