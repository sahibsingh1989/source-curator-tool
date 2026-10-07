# source-curator-tool

A small CLI that inspects a single web source and reports what the server returned.

- **Phase 1:** fetch a URL, report status/redirect/title/content-type.
- **Phase 2:** also detect an RSS/Atom feed linked from the page and preview recent items.
- **Phase 3:** save inspected sources to a local SQLite library so you don't have to
  re-research the same site twice.
- **Phase 4:** `discover` a topic to find candidate source websites via web search —
  finds candidates only; nothing is saved or approved automatically.
- **Phase 5:** `evaluate` a topic to discover candidates, inspect each one, and rank
  them by a transparent, explainable "curation score" — a prioritization heuristic,
  not a trust or authority judgment, and nothing is saved automatically.
- **Phase 6:** manage your saved library — approve/reject/reset status, add notes
  and tags, and `find` your own saved sources locally (no network).
- **Phase 7:** generate a Markdown review and a YAML config entry from a saved
  source, and export/validate those YAML config files.
- **Phase 8:** `check`/`check-all` re-visit saved sources and record whether
  they're still reachable and whether their feed is stale — a technical
  **health** signal, not a trust or quality judgment.

### `discover` vs. `evaluate` vs. `find` — which command do I want?

These three sound similar but do different things:

- **`discover`** — searches the **web** for candidate sources on a topic. Fast,
  cheap, no page fetching. Just cleaned-up search results.
- **`evaluate`** — also searches the web, but then **fetches and inspects every
  candidate** and ranks them with a curation score. Slower (one or two HTTP
  requests per candidate), more informative.
- **`find`** — searches **only your own saved library** in SQLite. No internet
  access at all. Use it once you've actually `add`ed sources and want to find one
  again by title, domain, URL, notes, or tag.

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
py source_tool.py list --status approved
py source_tool.py list --health stale
py source_tool.py list --status approved --health broken
```

```
#1    Example Domain                           example.com                    pending    Feed: No
#2    Hacker News                              news.ycombinator.com           pending    Feed: Yes
```

Prints `No sources saved yet.` when the library is empty, or a message naming
whichever of `--status`/`--health` excluded everything. `--status` must be one
of `pending`/`approved`/`rejected`; `--health` (Phase 8 — see below) must be
one of `healthy`/`stale`/`broken`, and reflects whatever `check`/`check-all`
last recorded (a never-checked source matches neither). The two filters can be
combined.

### Show one saved source

```
py source_tool.py show 1
```

```
ID:                  1
Requested URL:       https://example.com
Final URL:           https://example.com/
Domain:              example.com
Title:               Example Domain
Content-Type:        text/html; charset=utf-8
Feed URL:            (none detected)
Status:              pending
Tags:                (none)
Notes:               (none)
Added:               2026-10-02T01:23:02+00:00
Last checked:        2026-10-07T04:32:48+00:00
Last HTTP status:    200
Last reachable:      Yes
Last feed reachable: -
Last freshness:      Unknown
Health status:       HEALTHY
```

The `Last checked` through `Health status` rows are Phase 8's stored health
data; before the first `check`/`check-all`, they read `(never checked)` /
`(n/a)` / `-` / `(unchecked)`. An unknown ID prints a one-line error to stderr
and exits 1 rather than crashing.

### Manage saved sources: approve, reject, pending, note, tag (Phase 6)

```
py source_tool.py approve 1
py source_tool.py reject 2
py source_tool.py pending 1
```

Each sets the source's approval status and prints a one-line confirmation:

```
#1 Example Domain: status -> approved
```

Statuses are always exactly one of `pending` (the default when a source is
first added), `approved`, or `rejected`. Nothing is ever approved or rejected
**automatically** — these three commands are the only way a status changes.

```
py source_tool.py note 1 "Useful for stablecoin regulation and issuer disclosures"
```

Replaces the source's free-text curator note (stored in SQLite). Running `note`
again replaces the previous text.

```
py source_tool.py tag 1 stablecoins regulation tokenization
```

Replaces the source's tag set. Tags are lower-cased and de-duplicated
automatically (`Stablecoins regulation STABLECOINS` → `regulation, stablecoins`).
**Running `tag` again replaces the entire previous tag set** — it does not add
to it; re-tag with the full list you want.

Each of `approve`/`reject`/`pending`/`note`/`tag` prints a clean one-line error
to stderr and exits 1 for an ID that doesn't exist, instead of crashing.

### Search your saved library locally (Phase 6)

```
py source_tool.py find "stablecoin regulation"
py source_tool.py find "regulation" --status approved
```

Searches **only your local SQLite library** — title, domain, final URL, notes,
and tags. This never touches the internet; it is unrelated to `discover`/
`evaluate` (see the comparison near the top of this file).

Matching is case-insensitive and substring-based (partial words match), and
each query word that's found in a field scores a point for that field — so a
source matching more of your query words, or matching in more fields, ranks
higher. Results print best match first (ties broken by source ID, so the order
is always deterministic):

```
#1 Example Domain
   Domain: example.com
   Status: approved
   Tags:   regulation, stablecoins
   Notes:  Useful for stablecoin regulation and issuer disclosures
   Feed:   No
   Score:  3

Matches found: 1
```

`--status` filters to one approval status before searching. No matches prints
`No matches found for '<query>'.` and exits 0.

### Generate a Markdown review (Phase 7)

```
py source_tool.py review 1
py source_tool.py review 1 --overwrite
py source_tool.py review 1 --dir reviews --overwrite
```

Writes a Markdown file under `reviews/` (created automatically) for one saved
source. The filename is `<id>-<slugified domain>.md`, e.g. `1-example-com.md` —
deterministic, and the ID prefix guarantees it never collides with another
source even if two domains happen to slugify the same way.

The file clearly separates two kinds of content:

- **Curator notes** — exactly what you wrote with `note`, labeled "Written by
  a curator — not automatically generated."
- **Automatically collected metadata** — title, domain, content type, feed
  info, and date added, all pulled straight from the stored record, labeled
  "not a curator judgment."

A final "Why this source may be useful" section lists only what's derivable
from that stored metadata (tags present, a feed exists, notes exist, content
is machine-readable HTML) — **it never invents or asserts trustworthiness,
accuracy, or authority**; if there's nothing to go on, it says so explicitly
("No additional stored metadata is available to assess usefulness.").

If the review file already exists, `review` fails cleanly and tells you to
pass `--overwrite` rather than silently replacing (or silently skipping) it.

Options:

- `--overwrite` — replace an existing review file instead of refusing.
- `--dir PATH` — directory to write into (default: `reviews/`).
- `--db PATH` — same meaning as everywhere else.

### Generate a YAML config entry (Phase 7)

```
py source_tool.py config 1
py source_tool.py config 1 --output config/generated_sources.yaml
```

Builds a structured YAML entry for one **approved** source. A source that is
`pending` or `rejected` is refused with a clean error — `config` never exports
something that hasn't been explicitly approved:

```
Error: source #1 is not approved (status: pending) -- only approved sources can be exported to config.
```

With no `--output`, the entry prints to stdout as a one-item YAML list (see
the schema below). With `--output PATH`, it's appended to that file instead:
the file's parent directory is created if needed, the *existing* entries are
read back, and the whole file is re-written with the new entry added — so the
result is always one valid YAML document, never a broken append. If that
source's ID is already in the file, `config --output` fails cleanly instead of
creating a duplicate:

```
Error: source #1 is already present in config/generated_sources.yaml
```

**This never writes to any config file your project actually uses for
anything** — `--output` only ever points at a path you name explicitly, and
with no `--output` it only prints to your terminal.

### Export every approved source (Phase 7)

```
py source_tool.py export-approved
py source_tool.py export-approved --output config/approved_sources.yaml
```

Regenerates a YAML file (default `config/approved_sources.yaml`) **from
scratch** with every currently-approved source, sorted by ID. Unlike
`config --output`'s append behavior, this one fully overwrites the target file
each run, so it never includes a `pending` or `rejected` source, and it never
accumulates stale entries for sources that have since been un-approved.

### Validate a YAML config file (Phase 7)

```
py source_tool.py validate-config config/approved_sources.yaml
```

Parses the file as YAML (never raises on malformed input — a parse failure is
itself reported as a validation issue) and checks it against the schema
below: every entry must be a mapping with all required fields, `url` must
look like a real `http(s)://` URL, `id` values must be unique across the
file, and `approved` must be exactly `true`. Prints every issue found and
exits 1 if there's at least one; prints `OK: <path> is valid.` and exits 0
otherwise:

```
Invalid: config/broken.yaml
  - entry 0 (id=1): missing required field 'url'
  - entry 1 (id=1): duplicate id (first seen at entry 0)
```

### The YAML config schema (Phase 7)

Every entry generated by `config` or `export-approved` has this shape:

```yaml
- id: 1                              # the source's library ID (integer)
  name: Example Blog                 # title, or the domain if there's no title
  url: https://blog.example.com/     # the final (post-redirect) URL
  domain: blog.example.com
  type: feed                         # "feed" if a feed was detected, else "page"
  topics: [stablecoins, regulation]  # the source's tags
  approved: true                     # always true -- config only ever covers approved sources
  feed_url: https://blog.example.com/feed.xml  # present only when type is "feed"
```

Field order is always the same (as above), and the file is always a YAML
**list** of these mappings — even a single `config <id>` printed to stdout is
a one-item list — so any config file this tool produces can always be
concatenated or compared without reshaping.

### Re-check a saved source's health (Phase 8)

```
py source_tool.py check 1
```

Re-fetches the source's stored final URL (the same inspection logic as
`inspect`/`add`), re-checks its feed if it has one, and **persists** what it
finds in SQLite — this is the only thing that updates a source's health data.

```
#1 Example Domain
   Reachable:        Yes
   HTTP status:      200
   Final URL:        https://example.com/
   Feed found:       No
   Feed reachable:   (n/a)
   Latest feed item: (unknown)
   Freshness:        Unknown
   Health status:    HEALTHY
   Last checked:     2026-10-07T04:32:48+00:00
```

**"Health" here is purely technical — reachable? feed working? feed recently
updated? It says nothing about whether a source is trustworthy, accurate, or
worth keeping.** A perfectly healthy source can still be a bad one, and a
temporarily-unreachable source isn't necessarily broken for good.

### Re-check every saved source (Phase 8)

```
py source_tool.py check-all
```

Runs the same check against every saved source and prints a compact table —
one failing source never stops the rest from being checked:

```
#1    Example Domain                 200    Yes   No    Unknown    HEALTHY
#2    Internet Assigned Numbers Auth 200    Yes   No    Unknown    HEALTHY
#3    Hacker News                    200    Yes   Yes   Recent     HEALTHY
Checked 3 source(s): 3 healthy, 0 stale, 0 broken.
```

Columns are ID, title/domain, last HTTP status, reachable, feed present,
freshness, and health status.

### Health classification (Phase 8)

Deterministic, based only on what the check just observed — three buckets,
checked in this order (`source_curator/health.py`, function `classify_health`):

1. **BROKEN** — the page itself could not be reached, **or** a feed link was
   found on the page but fetching/parsing that feed failed.
2. **STALE** — the page is reachable, a feed exists and was fetched
   successfully, but its newest usable item date is more than 90 days old.
3. **HEALTHY** — reachable, with no feed failure, and freshness is Recent
   (≤30 days), Active (≤90 days), **or** Unknown (no feed, or no usable date
   in it) — everything that isn't BROKEN or STALE.

Freshness itself reuses the exact Phase 5 logic (`scoring.classify_freshness`)
— same thresholds, same UTC-aware date parsing, same "malformed dates become
Unknown, never a crash" behavior.

Options for both `check` and `check-all`:

- `--timeout SECONDS` — same meaning as `inspect`/`add` (default 10).
- `--db PATH` — same meaning as everywhere else.

`check <id>` on an unknown ID prints a clean error and exits 1; `check-all` on
an empty library prints `No sources saved yet.` and exits 0. Every stored
health field (`last_checked_at`, `last_http_status`, `last_reachable`,
`last_feed_reachable`, `last_freshness`, `last_health_status`, `last_error`)
is visible via `show` and filterable via `list --health`.

### Discover candidate sources for a topic (Phase 4)

```
py source_tool.py discover "stablecoin regulation"
```

Searches the web (via the [`ddgs`](https://pypi.org/project/ddgs/) package — no API
key needed) for the topic, cleans up the raw results, and prints a numbered
candidate list:

```
1. The GENIUS Act: A Comprehensive Guide to US Stablecoin Regulation
   Domain:  www.paulhastings.com
   URL:     https://www.paulhastings.com/insights/...
   Snippet: The GENIUS Act, the first federal law to create a comprehensive...
   Already saved: No

2. GENIUS Act Regulations on Payment Stablecoin ... - Federal Register
   Domain:  www.federalregister.gov
   URL:     https://www.federalregister.gov/documents/...
   Snippet: The Department of the Treasury proposes to issue regulations...
   Already saved: No

Raw results found:     10
Duplicates removed:    0
Unique source domains: 9
```

Cleanup applied before printing:
- **Exact/trivial duplicate URLs removed** — using the same URL normalization as
  the `add` command (trailing slash, default port, case, fragment).
- **One candidate per domain** — if a search turns up several pages from the same
  site, only the first is kept, so you don't see 10 articles from one domain.
- **Already-saved marking** — each candidate's domain is checked against your
  local source library (`--db`, same database as `add`/`list`/`show`) and marked
  `Already saved: Yes` or `No`. This is informational only.

**`discover` never saves, approves, scores, or ranks anything, and it does not
fetch/inspect the candidate pages themselves** — it only reports what the search
provider returned, cleaned up. Use `add <url>` afterward for any candidate you
decide is worth keeping.

Options:

- `--limit N` — maximum raw search results to request (default 20).
- `--db PATH` — database used only to mark already-saved domains (default:
  `data/sources.db`).

An empty topic, a zero/negative `--limit`, or a search-provider failure (network
error, rate limit, etc.) all print a one-line error to stderr and exit 1 instead of
crashing. Zero search results print `No results found for '<topic>'.` and exit 0.

### Evaluate and rank candidate sources for a topic (Phase 5)

```
py source_tool.py evaluate "stablecoin regulation" --limit 10
```

Runs the same discovery as `discover`, then automatically **inspects** each
candidate (fetches the page with `inspect`'s logic, checks for a feed, checks the
library for an existing save) and **ranks** them with a transparent 0-100
"curation score" — highest first:

```
1. The GENIUS Act: A Comprehensive Guide to US Stablecoin Regulation
   Domain:         www.paulhastings.com
   URL:            https://www.paulhastings.com/insights/...
   Curation score: 70/100
   Relevance:      High
   Reachable:      Yes
   Feed:           No
   Freshness:      Unknown
   Already saved:  No
   Why:            High query relevance; reachable; no feed detected.

2. ...

Raw results found:     10
Duplicates removed:    0
Unique source domains: 9
```

**The curation score is a prioritization heuristic for where to look first — it
is not a trust, reliability, or authority judgment, and `evaluate` never saves or
approves anything automatically.** It exists so you don't have to open every
discovered link by hand; deciding whether a source is actually good is still up
to you.

**How the score is built** (see `source_curator/scoring.py` — every piece below
is its own small, pure, testable function; no AI, no hidden or random inputs):

| Factor | Max points | How it's measured |
|---|---|---|
| Relevance | 40 | Do the query's words appear in the search result's title, its snippet, and/or the candidate's domain/page title? Each of those 3 fields is worth 1/3 of this factor. |
| Reachable / valid response | 20 | Did the page fetch succeed with an HTTP status in 200-399? |
| Feed availability | 15 (5 partial) | Full points if an RSS/Atom feed was found **and** fetched successfully; partial credit if a feed link was found but couldn't be fetched/parsed; zero if no feed link exists. |
| Freshness | 15 / 8 / 2 / 0 | From the feed's newest item date: Recent (≤30 days) / Active (≤90 days) / Old (>90 days) / Unknown (no feed, or no usable date). |
| Machine-readable content type | 10 | Full points if the page's `Content-Type` is HTML; zero otherwise (or if unreachable). |

Points are summed and rounded to one integer, 0-100. The printed "Why" line is
built from the same reason strings the scorer attaches to each factor, e.g.
`"High query relevance; reachable; RSS feed detected; recent feed activity."` —
nothing in the explanation is generated separately from the score itself.

**Freshness dates:** a feed item's RSS `pubDate` (RFC 822/2822) or Atom `updated`
(ISO 8601) string is parsed into a timezone-aware UTC datetime. A date that can't
be parsed is simply treated as missing (`Unknown`) — it never raises or aborts
the run.

**Failures are isolated per candidate:** if one candidate's page is down, times
out, or blocks the request, it still appears in the ranked list — just reachable
`No`, with the exact failure reason in its "Why" line and a correspondingly low
score. One broken site never aborts the whole `evaluate` run.

Options:

- `--limit N` — maximum candidates to discover **and** inspect (default 10; note
  this default differs from `discover`'s default of 20). `evaluate` never
  inspects more than this many candidates.
- `--timeout SECONDS` — per-candidate page/feed fetch timeout (default 10),
  same meaning as on `inspect`/`add`.
- `--db PATH` — database used only to mark already-saved domains (default:
  `data/sources.db`).

Empty topics, bad `--limit` values, and search-provider failures are handled the
same way as `discover` (clean one-line error, exit 1); zero search results print
`No results found for '<topic>'.` and exit 0.

## Tests

```
py -m unittest discover -s tests -v
```

Tests mock `requests.get`, the `ddgs` search call, and (for `evaluate`,
`check`/`check-all`) the page inspector itself, so they never touch the
network, and every library-backed command's tests (`add`/`list`/`show`/
`discover`/`evaluate`/`approve`/`reject`/`pending`/`note`/`tag`/`find`/
`review`/`config`/`export-approved`/`check`/`check-all`) always point `--db`
at a temporary SQLite file — never your real `data/sources.db` — and
`review`/`config`/`export-approved` tests always write into a temporary
directory, never your real `reviews/`/`config/`. `find` and `validate-config`
need no network or filesystem mocking beyond that, since neither leaves your
machine.

## Layout

```
source_tool.py            CLI entry point
source_curator/
  inspector.py            URL validation, page fetching, title extraction
  feeds.py                feed link discovery, fetching, parsing (feedparser)
  library.py              SQLite-backed source library (stdlib sqlite3, no ORM);
                           status/notes/tags, health fields, local find(), migration
  discovery.py            web search (ddgs) + candidate dedup/grouping
  scoring.py              relevance/freshness/curation-score math (pure, no I/O)
  evaluation.py           orchestrates discovery + inspection + scoring for `evaluate`
  review.py               Markdown review generation (metadata vs. curator notes)
  config_export.py        YAML entry building, file append/export, validate-config
  health.py               health classification + check_source (pure except inspect_fn)
  cli.py                  argparse wiring and output formatting
tests/
  test_inspector.py
  test_feeds.py
  test_library.py
  test_discovery.py
  test_scoring.py
  test_evaluation.py
  test_review.py
  test_config_export.py
  test_health.py
  test_cli.py
data/
  sources.db              created on first library-backed command's run; gitignored
reviews/
  <id>-<domain>.md         created by `review`; gitignored
config/
  *.yaml                   created by `config --output`/`export-approved`; gitignored
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
- Approval status always starts as `pending` when a source is first added; it
  only ever changes via the explicit `approve`/`reject`/`pending` commands
  (Phase 6) — never automatically.
- `list`/`show` read whatever is already in the database; they don't re-check
  that a saved source is still reachable.

Phase 4:
- `ddgs` is an unofficial, best-effort wrapper around a public search engine with
  no API key or SLA — result counts, ranking, and availability can vary run to
  run, and it can occasionally raise on rate limiting.
- "One candidate per domain" keeps strictly the *first* result seen per domain,
  not the most relevant one for the topic.
- "Already saved" is a **domain-level** check, not an exact-URL check: if you've
  saved any page from a domain, every candidate on that domain is marked as
  saved, even if the specific URL is new.
- No pagination/retry/backoff around the search provider — a single `--limit`
  request is made per run.

Phase 5:
- The curation score is deliberately simple and transparent — it is **not** a
  trust, authority, or fact-check signal. A high score just means "relevant,
  reachable, offers a feed, looks active, and is machine-readable"; a scam site
  that happens to be all of those would still score well.
- Freshness only looks at **feed** item dates. A page with no feed (or an
  unreachable one) is always `Unknown` freshness, even if the page itself is
  updated constantly.
- Relevance is pure word-overlap (lower-cased, alphanumeric tokens) between the
  query and the search title/snippet/domain/page title — it has no concept of
  synonyms, phrases, or semantic meaning.
- `evaluate` makes one HTTP request (plus, if a feed is found, one more) per
  candidate, up to `--limit` times, every run — there is no caching between
  runs, so re-running the same query re-fetches every candidate.
- "One candidate per domain" (inherited from `discover`) means `evaluate` never
  compares two pages from the same domain against each other.

Phase 6:
- `find`'s relevance score is simple substring/word-count matching, not the
  same tokenized-overlap scoring used in Phase 5 — the two are intentionally
  different (one ranks *your own saved library*, the other ranks *web search
  candidates*) and are not comparable to each other.
- `tag` always **replaces** the full tag set; there is no "add one tag" or
  "remove one tag" command yet — re-tag with everything you want kept.
- `note` always replaces the full note text; there is no append mode.
- `approve`/`reject`/`pending` don't require any particular prior status —
  you can, for example, `reject` a source that's already `rejected` (it's a
  no-op in effect, but not treated as an error).
- The schema migration (`library._migrate_schema`) only ever **adds** missing
  columns (`notes`, `tags`) to an existing `sources` table; it has no general
  migration framework for future schema changes, and it never drops, renames,
  or rewrites a column or any existing row's data.

Phase 7:
- The "Why this source may be useful" section in a review is a fixed set of
  deterministic, metadata-only observations (has tags? has a feed? has notes?
  HTML content?) — it has no deeper understanding of the source's actual
  subject matter beyond what's already in title/tags/notes.
- `review`'s filename is based on the **domain only** (`<id>-<domain-slug>.md`);
  two sources on the same domain get distinguishable filenames only because of
  the leading ID, not because the slug itself differs.
- `config`/`export-approved` re-serialize the *entire* YAML file on every
  write (rather than doing a true in-place text edit), so any manual
  formatting/comments a human added to that file by hand would be lost on the
  next append or export. Treat these files as generated output, not something
  to hand-edit and expect to survive.
- `config`'s "already present" duplicate check is purely by numeric source
  **ID** — if you deleted a source's row from SQLite and re-added the same URL
  (getting a new ID), `config` would treat it as a different entry even though
  it represents the same real-world source.
- `validate-config` checks shape and field validity (required fields, no
  duplicate IDs, syntactically valid URLs, `approved: true`) — it does not
  check that the URL is actually reachable, or that the referenced source
  still exists in your SQLite library.

Phase 8:
- "Health" is a point-in-time technical snapshot from the moment you last ran
  `check`/`check-all` — nothing re-checks it automatically (no scheduling, by
  design), so it goes stale itself the moment a once-healthy site goes down.
- STALE and freshness only ever look at the **feed**'s item dates, exactly
  like Phase 5 — a feedless page is always Unknown freshness (and therefore
  HEALTHY, never STALE) no matter how outdated its actual content is.
- `check`/`check-all` re-fetch the source's stored **final_url**, not the
  original `requested_url` — if a site's redirect target has changed since it
  was added, the check follows the new redirect rather than re-discovering it.
- `check-all` checks every saved source regardless of its approval status —
  there's no `--status`/`--health` pre-filter on `check-all` itself (use
  `list --status ... ` to see candidates first, then `check` them one by one,
  if you want to limit which ones get re-checked).
- BROKEN vs. STALE is intentionally coarse: a feed that fetches fine but
  whose newest item is 91 days old is STALE; a feed that fails to fetch even
  once is BROKEN regardless of how fresh its last known content was.
