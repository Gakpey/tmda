# tmda — Task Markdown for Agents

[![CI](https://github.com/Gakpey/tmda/actions/workflows/ci.yml/badge.svg)](https://github.com/Gakpey/tmda/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/tmda.svg)](https://pypi.org/project/tmda/)
[![Python](https://img.shields.io/pypi/pyversions/tmda.svg)](https://pypi.org/project/tmda/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

`.tmda` files are **task ledgers**: Markdown checkboxes with stable ids, a
machine-computed progress block, and a validated link to the detailed brief
that explains what the work is actually for.

Two rules define the format:

1. **Every task is addressable** — `- [!] T-014 Register the callback URL`,
   so an agent can reference, block on, and flip a specific piece of work
   instead of guessing which bullet was meant.
2. **Progress is derived, never authored** — the percentage and bar are
   recomputed from the checkboxes by tooling. A stale number is a validation
   error, not a convenience.

The prose — what the person wants to do, why, constraints, what "done" looks
like — lives in a linked `.md` brief. Every `.tmda` file is required to point
at one, and `tmda check` verifies the file *and* the anchor resolve.

```
project/
├── auth-flow-brief.md     ← prose: what we want to do and why
└── auth-flow.tmda         ← ledger: checkboxes, ids, progress
```

Full normative specification: **[SPEC.md](./SPEC.md)**.

---

## Install

```console
pipx install tmda          # recommended: isolates the CLI
```

or, inside a project environment:

```console
pip install tmda
```

with [uv](https://docs.astral.sh/uv/):

```console
uv tool install tmda
```

PyYAML is optional — `pip install "tmda[yaml]"` widens front-matter support to
full YAML. Without it a documented YAML subset is parsed and anything outside
it is a clear `E002` error.

Requires Python ≥ 3.9. Single module, standard library only.

From source:

```console
git clone https://github.com/Gakpey/tmda.git && cd tmda
pip install -e .
```

## Quickstart

```console
$ tmda init auth-flow --title "Sign-in and session overhaul"
created  auth-flow-brief.md
created  auth-flow.tmda
progress inserted
next:    edit auth-flow.tmda and run `tmda check`
```

Write the actual description in `auth-flow-brief.md`, then break the work down
in the ledger:

```markdown
## 2. Implementation

- [ ] T-010 Scaffold the auth package
  - [x] T-011 Add the provider SDK
  - [~] T-012 Wire the callback route
- [!] T-014 Register the callback URL @blocked-by=T-015
- [ ] T-015 Ask infra for the production redirect URI @owner=agent:scout @due=2026-10-28
- [ ] T-016 Rate limit the token endpoint @detail=./auth-flow-brief.md#rate-limiting @weight=3
```

Five checkbox states: `[ ]` todo · `[~]` in progress · `[x]` done ·
`[!]` blocked · `[-]` cancelled (excluded from the total). Metadata after the
description is parsed right-to-left: `@detail`, `@owner`, `@blocked-by`,
`@weight`, `@due`.

```console
$ tmda sync          # (re)write the managed progress block
$ tmda check         # validate everything; exit 1 on errors
tmda check: 2 file(s)

OK - 0 error(s), 0 warning(s)
```

## Reading progress

```console
$ tmda status examples/auth-flow.tmda
examples/auth-flow.tmda
  Sign-in and session overhaul
  brief    ./auth-flow-brief.md  ok
  status   ✖ active
  progress `█████████░░░░░░░░░░░`  **6/14 pts (43%)**
           5 done - 1 in progress - 1 blocked - 6 todo - 1 cancelled

  sections
    ▸  4/5 (80%)  1. Research and design
    ✖  1/7 (14%)  2. Implementation
    ▸  1/2 (50%)  3. Verification

  blocked
    T-014  Register the callback URL with the provider  blocked-by T-015

  next up
    T-005  Pick the rotation interval
    T-012  Wire the callback route
    ...
```

For agents, `tmda status --json` returns the same thing as data: derived
status, per-section rollups, every task with its container/parent links, plus
the `next` (dependency-satisfied) and `blocked` id lists. The stable shape is
documented in [SPEC.md §14.3](./SPEC.md#143-json-reports).

### The agent loop

```console
tmda status --json tasks/     # 1. what stands, what is blocked, what is next
$ less tasks/auth-flow-brief.md   # 2. read WHY before touching any checkbox
$ tmda mark T-015 done            # 3. record the outcome by id
$ tmda check --strict tasks/      # 4. hand back only if this exits 0
```

Step 2 is not optional — the ledger says *what*, the brief says *why*, and
*why* decides how the work gets done.

## Commands

| Command | Purpose |
|---|---|
| `tmda init NAME` | Scaffold a ledger and its brief stub |
| `tmda check [PATH…]` | Validate syntax, ids, links and the progress block (`--strict`, `--json`) |
| `tmda status [PATH…]` | Progress report per file, section and project (`--json`) |
| `tmda sync [PATH…]` | Rewrite the progress block in place; idempotent |
| `tmda list [PATH…]` | Flat task list with `--state`, `--open`, `--owner`, `--blocked`, `--id` |
| `tmda mark T-014 done` | Change a task's state by id and refresh progress |

Exit codes: `0` ok · `1` validation errors · `2` usage error.

## Use it in your CI

A composite action that installs tmda and runs it over your ledgers:

```yaml
# .github/workflows/tmda.yml
name: tmda
on: [push, pull_request]

jobs:
  tmda:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
      - uses: Gakpey/tmda@v1
        with:
          path: tasks/        # default "."
          strict: "true"      # default - warnings fail the build
          version: latest     # or pin "1.0.0"
```

`strict` is on by default because that is where the value is: a stale progress
block, a broken link into a brief, or a duplicated task id all fail the build
instead of quietly rotting.

## Design decisions worth knowing

**Progress is computed, and only computed.** There is no `percent:` key. The
single source of truth is the checkboxes, and `tmda check` fails if the
rendered block drifts from them (`E040`) — so the number in a diff is always
reconstructible.

**Parents don't double-count.** A task with nested children is a *container*:
its state is derived from them and it is excluded from the denominator.
Nesting therefore never inflates or deflates the percentage.

**Cancelled work leaves the fraction.** Dropping a task must not make progress
go backwards, so `- [-]` is excluded from both numerator and denominator.

**The brief is mandatory.** `detail:` is a required front-matter key, its
target must exist, and a `#fragment` must match a real heading. Task-level
`@detail=./brief.md#rate-limiting` gives one-hop access from any checkbox to
the paragraph that justifies it.

**Links run both ways.** A brief that doesn't mention its ledger warns (`W066`),
so a human reading the prose can always find the checkboxes.

**Ids, not line numbers.** `T-014` survives edits; a line number does not.
Everything else in the tool — `mark`, `@blocked-by`, the JSON reports — is
built on that.

## Repository layout

```
tmda/
├── SPEC.md                    normative format specification
├── README.md                  this file
├── tmda.py                    single-file, stdlib-only CLI
├── action.yml                 reusable GitHub Action
├── pyproject.toml             packaging, lint config
├── .github/workflows/         CI + PyPI release
├── examples/                  two ledger/brief pairs
└── tests/                     68 tests
```

Working on it:

```console
$ pip install -e ".[dev,yaml]"
$ python3 -m unittest discover -s tests     # 68 tests
$ ruff check .
$ tmda check --strict examples
```

See [CONTRIBUTING.md](./CONTRIBUTING.md) for the ground rules — notably
*standard library only* and *the format is versioned, so spec version 1 does
not change*.

## Status

Spec version `1`, initial release. The format is deliberately conservative:
candidates for a future version are listed in
[SPEC.md §16](./SPEC.md#16-versioning-and-future-work).

## License

[MIT](./LICENSE) © Joshua Gakpey.
