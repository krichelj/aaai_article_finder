# AAAI Article Finder

Find a paper in the [AAAI proceedings](https://ojs.aaai.org/index.php/AAAI/issue/archive) by scanning OJS issue pages in parallel using [Playwright](https://playwright.dev/python/).

The script discovers all issues for a given conference (or auto-detects the latest), then opens concurrent browser tabs to scan each issue for a matching paper title and/or author surnames (word-boundary matched to avoid false positives like "Ngu" in "Language").

## Setup

```bash
cd aaai_article_finder
uv venv && source .venv/bin/activate
uv pip install playwright
python -m playwright install chromium
```

## Usage

```bash
# Auto-detect latest conference, use default title/authors
uv run aaai_article_finder.py

# Target a specific conference
uv run aaai_article_finder.py --series "AAAI-26"

# Search for a different paper entirely
uv run aaai_article_finder.py \
  --series "AAAI-25" \
  --title "Some Other Paper Title" \
  --authors Smith Jones Garcia

# Crank up parallelism
uv run aaai_article_finder.py --workers 15
```

### Options

| Flag | Description | Default |
|---|---|---|
| `--series` | Conference label filter (e.g. `AAAI-26`, `Vol. 40`) | Auto-detect latest |
| `--title` | Paper title or unique substring to match | Hardcoded default |
| `--authors` | Author surnames (space-separated) | Hardcoded default |
| `--workers` | Number of parallel browser tabs | `10` |

## How It Works

1. Loads the OJS [archive page](https://ojs.aaai.org/index.php/AAAI/issue/archive) and collects all issue URLs for the target conference.
2. Spawns `--workers` concurrent Playwright tabs, each scanning one issue page.
3. A page is a **hit** if it contains the title substring, or if ≥3 author surnames match (using `\b` word boundaries).
4. On a hit, extracts the specific article link from the issue page.

## Defaults

If no `--title` or `--authors` are provided, the script searches for:

> **Consistency-based Abductive Reasoning over Perceptual Errors of Multiple Pre-trained Models in Novel Environments**
>
> Leiva, Ngu, Kricheli, Taparia, Senanayake, Shakarian, Bastian, Corcoran, Simari