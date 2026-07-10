"""
Built-in web tools: search the web, and fetch a URL as plain text.

Two tools:
- web_search(query): runs a search and returns the top results (title, URL,
  snippet) so the model can find relevant pages instead of guessing URLs.
- fetch_url(url): downloads a specific page/file and returns it as text.

Both use only the standard library for HTML handling (no BeautifulSoup
dependency): scripts/styles are dropped, tags stripped, whitespace collapsed.

Search backend: DuckDuckGo's "lite" HTML endpoint, which requires no API key
and no login — a good fit for Llampaca's free/local ethos. (Google can't be
scraped directly: it answers automated requests with a consent/login wall
instead of results, which is exactly why the model's earlier attempts failed.)
"""

import html
import re

import requests

# Cap on the text returned to the model — web pages can be huge and local
# models have small context windows.
MAX_PAGE_CHARS = 6000

# Number of search results returned to the model. A handful is enough for the
# model to pick a page and stay within its small context window.
MAX_SEARCH_RESULTS = 6

# DuckDuckGo's no-JavaScript endpoints. They must be queried with POST: a GET
# returns an HTTP 202 anti-bot challenge, while POST returns real results.
# We try "lite" first and fall back to "html": they have different markup and
# different anti-bot behavior, so one may work when the other doesn't.
DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
DDG_HTML_URL = "https://html.duckduckgo.com/html/"

# Pretend to be a browser: several sites (and DuckDuckGo) return 403/challenge
# pages to unknown user agents.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


def _strip_html(fragment: str) -> str:
    """Turn an HTML fragment into clean plain text (entities decoded)."""
    return html.unescape(re.sub(r"<[^>]+>", " ", fragment)).strip()


def _is_ad(url: str) -> bool:
    """
    True if a result URL is a DuckDuckGo advertisement redirect rather than
    an organic result (ads point at duckduckgo.com/y.js with an ad_domain
    parameter). Ads would otherwise pollute the model's context with long
    tracking URLs of no informational value.
    """
    return "y.js" in url or "ad_domain=" in url


def _parse_lite_results(page: str):
    """
    Parse results from the "lite" endpoint markup.

    There, each result is an anchor whose href is the real target URL,
    followed by class='result-link', with the abstract in a sibling table
    cell of class 'result-snippet'. Returns [(url, title, snippet), ...].
    """
    links = re.findall(
        r"<a[^>]*href=\"([^\"]+)\"[^>]*class=['\"]?result-link['\"]?[^>]*>(.*?)</a>",
        page,
        re.S,
    )
    snippets = re.findall(
        r"class=['\"]?result-snippet['\"]?[^>]*>(.*?)</td>",
        page,
        re.S,
    )
    return [
        (url, title, snippets[i] if i < len(snippets) else "")
        for i, (url, title) in enumerate(links)
        if not _is_ad(url)
    ]


def _parse_html_results(page: str):
    """
    Parse results from the "html" endpoint markup.

    There, result anchors have class 'result__a' and their href is a
    DuckDuckGo redirect (//duckduckgo.com/l/?uddg=<url-encoded target>), so
    the real URL must be extracted from the 'uddg' query parameter.
    Snippets are anchors of class 'result__snippet'.
    """
    from urllib.parse import parse_qs, unquote, urlparse

    links = re.findall(
        r"<a[^>]*class=\"result__a\"[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>",
        page,
        re.S,
    )
    snippets = re.findall(
        r"class=\"result__snippet\"[^>]*>(.*?)</a>",
        page,
        re.S,
    )

    results = []
    for i, (href, title) in enumerate(links):
        if _is_ad(href):
            continue  # skip sponsored results (see _is_ad)
        url = href
        if "uddg=" in href:
            # Decode the redirect wrapper to get the real destination URL
            query = urlparse(html.unescape(href)).query
            url = unquote(parse_qs(query).get("uddg", [href])[0])
        results.append((url, title, snippets[i] if i < len(snippets) else ""))
    return results


def web_search(query: str) -> str:
    """
    Search the web and return the top results as a list of title, URL and
    snippet. Use this to find pages when you don't already know the URL, then
    optionally call fetch_url on a promising result to read the full page.

    Args:
        query: The search query, in natural language.
    """
    # Try each endpoint in turn; markup differs per endpoint so each has its
    # own parser. One endpoint occasionally returns an empty/challenge page
    # for a query the other answers fine, so falling back matters.
    attempts = [
        (DDG_LITE_URL, _parse_lite_results),
        (DDG_HTML_URL, _parse_html_results),
    ]
    errors = []
    for endpoint, parser in attempts:
        try:
            response = requests.post(
                endpoint,
                data={"q": query},
                headers=_HEADERS,
                timeout=15,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            errors.append(str(e))
            continue

        results = parser(response.text)
        if results:
            lines = [f"Search results for '{query}':\n"]
            for index, (url, title, snippet) in enumerate(results[:MAX_SEARCH_RESULTS]):
                lines.append(f"{index + 1}. {_strip_html(title)}")
                lines.append(f"   URL: {url}")
                clean_snippet = _strip_html(snippet)
                if clean_snippet:
                    lines.append(f"   {clean_snippet}")
            return "\n".join(lines)

    if errors:
        return f"Error running web search: {'; '.join(errors)}"
    return (
        f"No search results found for '{query}'. "
        "Try rephrasing with fewer, more specific keywords."
    )


def _html_to_text(html_content: str) -> str:
    """Convert HTML to rough plain text using only the standard library."""
    # Remove non-content blocks entirely (their text is useless to the model)
    text = re.sub(r"(?is)<(script|style|noscript|head)[^>]*>.*?</\1>", " ", html_content)
    # Turn structural tags into newlines so paragraphs stay readable
    text = re.sub(r"(?i)<(br|/p|/div|/li|/h[1-6]|/tr)[^>]*>", "\n", text)
    # Strip every remaining tag
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    # Decode HTML entities (&amp;, &egrave;, ...)
    text = html.unescape(text)
    # Collapse runs of whitespace but keep line structure
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def fetch_url(url: str) -> str:
    """
    Download a web page or file and return its content as plain text.

    Args:
        url: The full URL to fetch (must start with http:// or https://).
    """
    if not url.startswith(("http://", "https://")):
        return "Error: URL must start with http:// or https://"

    try:
        response = requests.get(url, headers=_HEADERS, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        return f"Error fetching URL: {e}"

    content_type = response.headers.get("content-type", "")
    if "html" in content_type:
        text = _html_to_text(response.text)
    else:
        # JSON, plain text, etc: return as-is
        text = response.text

    if len(text) > MAX_PAGE_CHARS:
        text = text[:MAX_PAGE_CHARS] + f"\n... [truncated: page is {len(text)} characters]"
    return text


def register_web_tools(registry) -> None:
    """Register the web tools on the given ToolRegistry."""
    registry.register(web_search)
    registry.register(fetch_url)
