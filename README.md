# source-curator-tool

A small CLI that inspects a single web source and reports what the server returned.

- **Phase 1:** fetch a URL, report status/redirect/title/content-type.
- **Phase 2:** also detect an RSS/Atom feed linked from the page and preview recent items.

## Setup

Python 3.13.

```
py -m pip install -r requirements.txt
```

## Usage

```
py source_tool.py inspect https://example.com
```

Output:

```
Status:       200
Final URL:    https://example.com/
Domain:       example.com
Title:        Example Domain
Content-Type: text/html; charset=UTF-8

Feed found:  No
```

When the page links a feed via `<link rel="alternate" type="application/rss+xml" ...>`
(or `application/atom+xml`), the feed is fetched and up to 5 recent items are shown:

```
Feed found:  Yes
Feed URL:    https://blog.example.com/feed.xml
Feed title:  Example Blog
Items (2):
  1. First Post
     Link:      https://blog.example.com/first-post
     Published: Wed, 01 Oct 2025 12:00:00 GMT
  2. Second Post
     Link:      https://blog.example.com/second-post
     Published: Tue, 30 Sep 2025 09:00:00 GMT
```

If a feed link is found but can't be fetched or parsed, that's reported instead of a
crash:

```
Feed found:  Yes
Feed URL:    https://blog.example.com/feed.xml
Feed error:  Could not connect to feed URL
```

Options:

- `--timeout SECONDS` — request timeout, applied to both the page fetch and the feed
  fetch (default 10).

Only `http` and `https` URLs are accepted. Redirects are followed and the final URL
is reported. Connection failures, timeouts and redirect loops on the **page** fetch
print a one-line error to stderr and exit with status 1; a successful page fetch
exits 0 even for a 404. Feed detection only runs for HTML pages, and feed-fetch
failures never fail the overall command — they're reported in the `Feed error:` line.

## Tests

```
py -m unittest discover -s tests -v
```

Tests mock `requests.get`, so they never touch the network.

## Layout

```
source_tool.py            CLI entry point
source_curator/
  inspector.py            URL validation, page fetching, title extraction
  feeds.py                feed link discovery, fetching, parsing (feedparser)
  cli.py                  argparse wiring and output formatting
tests/
  test_inspector.py
  test_feeds.py
  test_cli.py
```

## Known limitations (Phase 2)

- Only the **first** matching `<link rel="alternate">` feed is used when a page
  links more than one.
- Feed items are shown using whatever the feed itself reports for a publish date
  (RSS `pubDate` / Atom `updated`) — not normalized to a single format.
- No feed discovery beyond `<link>` tags (e.g. no guessing `/feed` or `/rss.xml`
  if the page doesn't declare one) — that's a Phase 3+ concern (web search /
  discovery is explicitly out of scope for now).
