# source-curator-tool

A small CLI that inspects a single web source and reports what the server returned.

- **Phase 1:** fetch a URL, report status/redirect/title/content-type.
- **Phase 2:** also detect an RSS/Atom feed linked from the page and preview recent items.
- **Phase 3:** save inspected sources to a local SQLite library so you don't have to
  re-research the same site twice.

## Setup

Python 3.13.

```
py -m pip install -r requirements.txt
```

## Usage

### Inspect (Phase 1 + 2)

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

### Add a source to the local library (Phase 3)

```
py source_tool.py add https://example.com
```

Runs the same inspection as `inspect`, then saves the result (original URL, final
URL, domain, title, content type, detected feed URL if any, an added-at timestamp,
and an approval status that always starts as `pending`) to a local SQLite database.
Prints the new source's ID and a one-line summary:

```
Saved source #1: Example Domain (example.com)
```

**Duplicates:** the final URL is normalized (lower-cased scheme/host, default port
and fragment dropped, trailing slash collapsed) before checking for an existing
match. An exact or trivially-equivalent URL is **not** saved again:

```
Not saved: already saved as #1 (https://example.com/)
```
(exit status 1, nothing written)

**Same domain, different URL:** allowed, but flagged, since one domain can
legitimately host more than one useful source:

```
Saved source #2: Another Page (example.com)
Note: domain 'example.com' already has 1 saved source(s): #1
```

Options:

- `--timeout SECONDS` — same as `inspect`.
- `--db PATH` — path to the SQLite file (default: `data/sources.db`). The
  directory and schema are created automatically if they don't exist yet.

### List saved sources

```
py source_tool.py list
```

```
#1    Example Domain                           example.com                    pending    Feed: No
#2    Hacker News                              news.ycombinator.com           pending    Feed: Yes
```

Prints `No sources saved yet.` when the library is empty.

### Show one saved source

```
py source_tool.py show 1
```

```
ID:            1
Requested URL: https://example.com
Final URL:     https://example.com/
Domain:        example.com
Title:         Example Domain
Content-Type:  text/html; charset=utf-8
Feed URL:      (none detected)
Status:        pending
Added:         2026-10-02T01:23:02+00:00
```

An unknown ID prints a one-line error to stderr and exits 1 rather than crashing.

## Tests

```
py -m unittest discover -s tests -v
```

Tests mock `requests.get`, so they never touch the network, and `add`/`list`/`show`
tests always point `--db` at a temporary SQLite file — never your real
`data/sources.db`.

## Layout

```
source_tool.py            CLI entry point
source_curator/
  inspector.py            URL validation, page fetching, title extraction
  feeds.py                feed link discovery, fetching, parsing (feedparser)
  library.py              SQLite-backed source library (stdlib sqlite3, no ORM)
  cli.py                  argparse wiring and output formatting
tests/
  test_inspector.py
  test_feeds.py
  test_library.py
  test_cli.py
data/
  sources.db              created on first `add`/`list`/`show` run; gitignored
```

## Known limitations

Phase 2:
- Only the **first** matching `<link rel="alternate">` feed is used when a page
  links more than one.
- Feed items are shown using whatever the feed itself reports for a publish date
  (RSS `pubDate` / Atom `updated`) — not normalized to a single format.
- No feed discovery beyond `<link>` tags (e.g. no guessing `/feed` or `/rss.xml`
  if the page doesn't declare one) — that's explicitly out of scope for now.

Phase 3:
- Duplicate detection is based on a normalized **final URL** only; it will not
  notice that two different URLs happen to render the same content.
- Approval status is always saved as `pending` — there is no command yet to
  change it (no automatic approval, by design).
- `list`/`show` read whatever is already in the database; they don't re-check
  that a saved source is still reachable.
