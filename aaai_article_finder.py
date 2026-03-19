#!/usr/bin/env python3
"""
Search AAAI proceedings on ojs.aaai.org for a specific paper.

Parallel archive scan using Playwright with concurrent browser tabs.

Requirements:
  uv pip install playwright
  python -m playwright install chromium
"""

import argparse
import asyncio
import re
from urllib.parse import urljoin

from playwright.async_api import async_playwright, Page, Browser, BrowserContext

# ── Defaults (override via CLI) ────────────────────────────────────────────
DEFAULT_TITLE = (
    "Consistency-based Abductive Reasoning over Perceptual Errors "
    "of Multiple Pre-trained Models in Novel Environments"
)

DEFAULT_AUTHORS = [
    "Leiva", "Ngu", "Kricheli", "Taparia",
    "Senanayake", "Shakarian", "Bastian", "Corcoran", "Simari",
]

DEFAULT_CONCURRENCY = 10
DELAY_MS = 500
MIN_AUTHOR_MATCHES = 3


# ── CLI ────────────────────────────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find a paper in the AAAI proceedings by scanning OJS issue pages in parallel."
    )
    parser.add_argument(
        "--series",
        help="Conference label to filter issues, e.g. 'AAAI-26'. Auto-detects latest if omitted.",
    )
    parser.add_argument(
        "--title",
        default=DEFAULT_TITLE,
        help="Paper title (or unique substring) to search for.",
    )
    parser.add_argument(
        "--authors",
        nargs="+",
        default=DEFAULT_AUTHORS,
        help="Author surnames to match (word-boundary). Default: %(default)s",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help="Number of parallel browser tabs. Default: %(default)s",
    )
    return parser.parse_args()


# ── Helpers ────────────────────────────────────────────────────────────────
async def safe_goto(page: Page, url: str, retries: int = 3) -> bool:
    for attempt in range(retries):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(DELAY_MS)
            return True
        except Exception:
            if attempt == retries - 1:
                return False
            await page.wait_for_timeout(2000)
    return False


def build_matchers(title: str, authors: list[str]) -> dict:
    """Build the matching config used by scan workers."""
    return {
        "title_fragment": title.lower()[:60],
        "surname_patterns": {
            name: re.compile(r'\b' + re.escape(name) + r'\b', re.IGNORECASE)
            for name in authors
        },
    }


def text_match(page_text: str, matchers: dict) -> dict:
    lower = page_text.lower()
    title_hit = matchers["title_fragment"] in lower
    author_hits = [
        name for name, pat in matchers["surname_patterns"].items()
        if pat.search(page_text)
    ]
    return {"title_hit": title_hit, "author_hits": author_hits}


def is_real_hit(match: dict) -> bool:
    return match["title_hit"] or len(match["author_hits"]) >= MIN_AUTHOR_MATCHES


# ── Discover issue URLs ───────────────────────────────────────────────────
BASE = "https://ojs.aaai.org/index.php/AAAI"


async def discover_issue_urls(page: Page, series: str | None = None) -> list[str]:
    print("  Discovering AAAI issue IDs from archive...")
    url = f"{BASE}/issue/archive"
    if not await safe_goto(page, url):
        print("  Failed to load archive page.")
        return []

    all_issues: list[tuple[str, str]] = []
    links = await page.query_selector_all('a[href*="/issue/view/"]')
    for link in links:
        href = await link.get_attribute("href") or ""
        text = (await link.inner_text()).strip()
        parent = await link.evaluate_handle("el => el.closest('li') || el.parentElement")
        parent_text = (await parent.inner_text()) if parent else ""
        combined = f"{text} {parent_text}"

        if "AAAI" not in combined:
            continue

        full_url = urljoin(url, href)
        all_issues.append((full_url, combined))

    if not all_issues:
        print("  No AAAI issues found on archive page.")
        return []

    # Auto-detect latest volume if no series specified
    if series is None:
        vol_match = re.search(r'Vol\.\s*(\d+)', all_issues[0][1])
        if vol_match:
            filter_str = f"Vol. {vol_match.group(1)}"
        else:
            tag_match = re.search(r'AAAI-\d+', all_issues[0][1])
            if tag_match:
                filter_str = tag_match.group(0)
            else:
                print("  Could not auto-detect conference. Use --series.")
                return []
        print(f"  Auto-detected latest conference: {filter_str}")
    else:
        filter_str = series

    issue_urls: list[str] = []
    for issue_url, combined in all_issues:
        if filter_str in combined and issue_url not in issue_urls:
            issue_urls.append(issue_url)

    return issue_urls


# ── Scan a single issue ───────────────────────────────────────────────────
async def scan_single_issue(
    context: BrowserContext,
    sem: asyncio.Semaphore,
    url: str,
    idx: int,
    total: int,
    matchers: dict,
) -> list[dict]:
    async with sem:
        page = await context.new_page()
        tag = f"[{idx}/{total}]"
        try:
            if not await safe_goto(page, url):
                print(f"  {tag} {url} — SKIP (failed)")
                return []

            page_text = await page.inner_text("body")
            match = text_match(page_text, matchers)

            if not is_real_hit(match):
                print(f"  {tag} {url} — no match")
                return []

            labels = []
            if match["title_hit"]:
                labels.append("TITLE")
            if match["author_hits"]:
                labels.append(f"authors: {', '.join(match['author_hits'])}")
            print(f"  {tag} {url} — HIT! ({'; '.join(labels)})")

            found = []
            article_links = await page.query_selector_all('a[href*="/article/view/"]')
            for link in article_links:
                title = (await link.inner_text()).strip()
                href = await link.get_attribute("href") or ""
                title_lower = title.lower()

                if "abductive" in title_lower or "consistency-based" in title_lower:
                    found.append({
                        "issue_url": url,
                        "article_title": title,
                        "article_url": urljoin(url, href),
                    })

            if not found:
                found.append({
                    "issue_url": url,
                    "article_title": "(check page manually)",
                    "article_url": url,
                    "matched_authors": match["author_hits"],
                    "matched_title": match["title_hit"],
                })
            return found
        finally:
            await page.close()


# ── Main ───────────────────────────────────────────────────────────────────
async def main():
    args = parse_args()
    matchers = build_matchers(args.title, args.authors)

    print("=" * 70)
    print("AAAI Proceedings Paper Finder")
    print(f"Title:   {args.title[:70]}...")
    print(f"Authors: {', '.join(args.authors)}")
    print(f"Workers: {args.workers}")
    print(f"Series:  {args.series or 'auto-detect latest'}")
    print("=" * 70)

    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        )

        page = await context.new_page()
        issue_urls = await discover_issue_urls(page, series=args.series)
        await page.close()

        if not issue_urls:
            print("\n❌  No issues found.")
            await browser.close()
            return

        print(f"  Scanning {len(issue_urls)} issue(s) with {args.workers} workers...")

        sem = asyncio.Semaphore(args.workers)
        total = len(issue_urls)
        tasks = [
            scan_single_issue(context, sem, url, i, total, matchers)
            for i, url in enumerate(issue_urls, 1)
        ]
        results_nested = await asyncio.gather(*tasks)
        results = [item for sublist in results_nested for item in sublist]

        if results:
            print("\n" + "=" * 70)
            print("✅  FOUND in AAAI proceedings:")
            for r in results:
                print(f"   Issue:   {r['issue_url']}")
                print(f"   Title:   {r.get('article_title', 'N/A')}")
                print(f"   Article: {r.get('article_url', 'N/A')}")
                if "matched_authors" in r:
                    print(f"   Authors: {r['matched_authors']}")
                print()
        else:
            print("\n❌  Paper NOT found.")
            print("   Try: https://ojs.aaai.org/index.php/AAAI/search/search")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())