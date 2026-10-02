# source-curator-tool

Phase 1: a small CLI that inspects a single web source and reports what the server returned.

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
```

Options:

- `--timeout SECONDS` — request timeout (default 10).

Only `http` and `https` URLs are accepted. Redirects are followed and the final URL
is reported. Connection failures, timeouts and redirect loops print a one-line error
to stderr and exit with status 1; a successful fetch exits 0 even for a 404.

## Tests

```
py -m unittest discover -s tests -v
```

Tests mock `requests.get`, so they never touch the network.

## Layout

```
source_tool.py            CLI entry point
source_curator/
  inspector.py            URL validation, fetching, title extraction
  cli.py                  argparse wiring and output formatting
tests/
  test_inspector.py
  test_cli.py
```
