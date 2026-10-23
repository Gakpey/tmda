# tmda — Task Markdown for Agents

**Extension:** `.tmda` &nbsp;·&nbsp; **Spec version:** `1` &nbsp;·&nbsp; **Status:** implemented (`tmda.py`), reference tests in `tests/`

---

## 1. Abstract

A `.tmda` file is a **task ledger**: a strict profile of CommonMark with YAML
front matter, checkboxes that carry stable machine-readable ids, and a
tool-computed progress block. It is meant to be read by two audiences at once —
a human skimming a diff, and an agent that needs to answer *"where does this
project actually stand, and what should I do next?"* without parsing prose.

Two rules define the format:

1. **Every task is addressable.** Each checkbox carries an id (`T-001`) so an
   agent can reference, filter, block on, and flip individual pieces of work
   without guessing which line is meant.
2. **Progress is derived, never authored.** Percentages, rollups and the
   progress bar are computed from the checkboxes by tooling. A stale or
   hand-written number is a validation error, not a convenience.

The *why* — the long-form description of what the person actually wants to do —
**never lives in the `.tmda` file**. It lives in a linked Markdown brief, and
every `.tmda` file is required to point at one.

```
project/
├── auth-flow-brief.md      ← prose: what we want, why, constraints, "done"
└── auth-flow.tmda          ← ledger: checkboxes, ids, progress
```

---

## 2. Goals and non-goals

### Goals

- A file an agent can load and get a trustworthy answer from in one read.
- Zero-friction authoring: it renders as ordinary Markdown on any GFM viewer.
- Deterministic, recomputable progress so that "43%" is never a lie.
- An explicit, validated link from every ledger to the detailed brief behind it.
- Stable ids so tasks can be referenced across a conversation, a PR and a
  commit.

### Non-goals

- Not a scheduling or Gantt tool. `@due` is informational; nothing computes
  dates.
- No cross-file task dependencies in v1. File-to-file ordering is expressed
  with front-matter `depends_on`; task-level `@blocked-by` is same-file only.
- No comment threads, review states, or assignee workflows beyond `@owner`.
- Not a replacement for the brief. Rich text, tables and diagrams belong there.
- Not a general-purpose TODO format. Unparsed bullets are allowed but carry no
  meaning.

---

## 3. Conventions

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY are to be interpreted
as described in RFC 2119.

Line numbers cited in diagnostics are 1-based and count from the first line of
the file, including the front matter.

---

## 4. File basics

| Property | Rule |
|---|---|
| Extension | `.tmda` (lowercase) |
| Encoding | UTF-8. A leading BOM is tolerated on read and preserved on write |
| Line endings | LF on write. CRLF files are read losslessly and rewritten as CRLF |
| Syntax | CommonMark + GFM task-list items + YAML front matter |
| Media type | `text/x-tmda` (proposed, unregistered) |

**Only `.tmda` files are parsed by tooling.** A linked brief is ordinary
Markdown: its headings, tables and its own `- [ ]` lines are inert. A brief
SHOULD use plain `-` bullets for anything that looks like a checklist, so no
reader mistakes it for tracked work.

Task-list indentation MUST use spaces. A tab anywhere in a task line is an
error (`E020`).

Fenced code blocks (``` or `~~~`) are opaque: headings, checkboxes and
progress markers inside them are not parsed. Content inside *indented* code
blocks is not treated specially — use fences.

---

## 5. Document structure

A `.tmda` file is the following sequence. Steps 2–4 are optional but their
relative order is fixed.

1. **Front matter** — `---` … `---`, starting at byte 0 (§6).
2. **H1 title** — at most one, SHOULD equal the front-matter `title`.
3. **Preamble** — MAY contain a blockquote pointing at the brief.
4. **Progress region** — exactly zero or one (§11).
5. **Body** — sections (headings level 2–6) containing task items (§7–§9).

```markdown
---
tmda: 1
id: auth-flow
title: Sign-in and session overhaul
detail: ./auth-flow-brief.md
status: active
owner: joshua
updated: 2026-10-22
---

# Sign-in and session overhaul

> **Brief:** [./auth-flow-brief.md](./auth-flow-brief.md) - what we are trying
> to do and why. Read it before changing any checkbox below.

<!-- tmda:progress:start -->
**Progress** `█████████░░░░░░░░░░░` **6/14 pts (43%)** - 1 in progress - 1 blocked - 6 todo - 1 cancelled
<!-- tmda:progress:end -->

## 1. Research and design

- [x] T-001 Audit the current login flow @owner=joshua @weight=2 @detail=./auth-flow-brief.md#what-we-want-to-do
```

---

## 6. Front matter

Front matter is a YAML mapping delimited by lines containing exactly `---`.
It MUST be the first thing in the file.

The built-in parser accepts a **YAML subset**: scalar values, inline lists
(`key: [a, b]`) and block lists. Nested mappings and anything outside that
subset raise `E002` with a message that says so. If PyYAML is installed it is
used instead and full YAML is accepted — `pip install "tmda[yaml]"` widens
what parses. Whether or not PyYAML is present must not change the outcome for
files inside the subset, and it must not emit extra diagnostics: `tmda check
--strict` is expected to behave identically on both paths.

### 6.1 Keys

| Key | Required | Type | Meaning |
|---|---|---|---|
| `tmda` | **yes** | int | Spec version. MUST be `1` |
| `id` | **yes** | slug | Stable file id, `^[a-z0-9][a-z0-9_-]*$`. Unique across the project |
| `title` | **yes** | string | Human title. SHOULD match the H1 |
| `detail` | **yes** | path | Relative path to the detailed brief this ledger tracks (§12) |
| `status` | no | enum | Authored posture: `planned` `active` `blocked` `done` `archived` |
| `owner` | no | string | Person or agent responsible for the file |
| `updated` | no | date | ISO `YYYY-MM-DD` of the last authored edit |
| `depends_on` | no | list | Other ledgers (by `id`) or paths this file waits on |
| `tags` | no | list | Freeform labels |
| `agent` | no | string | The agent currently driving this file |

Any other key produces warning `W068`.

### 6.2 `status` is authored, progress is derived

`status` is what a human *declares*. The tooling separately computes a
**derived status** (§10.4) and reports both. Two contradictions are warned
about (`W061`):

- `status: done` while live tasks remain;
- `status: planned|active|blocked` while every task is done.

`status: archived` is authored-only: archived files are excluded from project
totals but are still validated.

---

## 7. Task items

### 7.1 Grammar

```
task-line  := indent bullet WS "[" state "]" WS task-id WS text metadata*
indent     := " "*                       ; spaces only, may be empty
bullet     := "-" | "*" | "+"
state      := " " | "~" | "x" | "X" | "!" | "-"
task-id    := "T-" [0-9]{3,}
text       := non-empty description
metadata   := "@" key "=" value          ; zero or more, at end of line (§8)
```

Example:

```markdown
- [!] T-014 Register the callback URL with the provider @blocked-by=T-015
```

A bullet immediately followed by `[` and a **single character** is a task line.
A bullet followed by a longer bracketed token (`- [note]`) is an ordinary list
item and is ignored.

### 7.2 States

| Marker | State | Counts toward progress | Interactive on GitHub |
|---|---|---|---|
| `[ ]` | `todo` | yes | yes |
| `[~]` | `in-progress` | yes | no |
| `[x]` / `[X]` | `done` | yes | yes |
| `[!]` | `blocked` | yes | no |
| `[-]` | `cancelled` | **no** (excluded from the denominator) | no |

`[X]` is normalised to `done` on read and written back as `[x]`.
Any other character is an error (`E021`).

> **Renderer note.** GFM only makes `[ ]`, `[x]` and `[X]` interactive
> checkboxes; the other three render as literal bracketed text. They remain
> machine-readable and visually distinct, which is what the format needs. This
> trade-off is deliberate.

### 7.3 Ids

- Format `T-` followed by at least three digits: `T-001`, `T-0142`.
- The id MUST immediately follow the checkbox, separated by whitespace.
- Ids are unique **within a file** (§10.1). Across files, use front-matter
  `depends_on`.
- A missing id is an error (`E022`); a malformed one (`t-001`, `T-1`) is `E024`.

Ids are the format's contract with agents: `tmda mark T-014 done`,
`@blocked-by=T-015`, `blocked: ["T-014"]` in JSON all depend on them.

### 7.4 Indentation and containers

Nesting is determined by comparing the space-indent of consecutive task lines;
the step size is not fixed. A task with one or more nested children is a
**container**:

- a container's own marker is **ignored** — its state is derived from its
  children (§10.2);
- a container is **excluded** from the progress numerator and denominator, so
  parent and child are never double-counted;
- marking a container `[x]` while children are outstanding warns (`W062`);
- an indented task with no lower-indent parent warns (`W063`).

---

## 8. Task metadata

Metadata tokens sit at the end of a task line, after the description:

```
@key=value @key="quoted value"
```

Parsing runs **right-to-left**. An unquoted value ends at the first whitespace
and therefore MUST NOT contain spaces; wrap it in `"…"` or `'…'` if it must.
A `#fragment` needs no quoting. Duplicate keys on one line are an error
(`E034`).

### 8.1 Reserved keys

| Key | Type | Meaning |
|---|---|---|
| `detail` | path | Per-task link to the relevant part of a brief, e.g. `./brief.md#rate-limiting`. Overrides the file-level `detail` for this task |
| `owner` | string | Who (human or `agent:…`) is doing it |
| `blocked-by` | list | Comma-separated task ids in **this file** that must be `done` first. E.g. `@blocked-by=T-015,T-016` |
| `weight` | int ≥ 1 | Progress weight. Default `1` |
| `due` | date | ISO `YYYY-MM-DD`. Informational only |

Unknown keys parse fine and warn (`W069`) — they are carried through to JSON
untouched, so project-specific extensions survive a round-trip, but they are
flagged so typos surface.

`@blocked-by` does not change a task's state on its own. State is always
authoritative from the marker; `blocked-by` is what makes a task eligible (or
not) for the *next up* suggestion.

---

## 9. Sections

- A level-1 heading is the file title, not a section.
- Every heading of level 2 or deeper opens a section; nesting follows heading
  level, and sections roll up through their descendants.
- Tasks belong to the innermost heading open at their line. Tasks above the
  first heading are "unsectioned" and are reported separately while still
  counting toward file totals.
- Headings are ordinary Markdown. Put the section's detail link in the task
  metadata or the file-level `detail`, not in the heading.

---

## 10. Progress model

This section is normative. Any conforming implementation MUST produce the same
numbers for the same file.

### 10.1 Scope

Progress is computed over **non-container tasks** in the file (§7.4). Tasks
whose state is `cancelled` are excluded from the numerator *and* the
denominator — dropping work must not move the percentage backwards.

### 10.2 Derived container state

Children are always resolved before their parent. For a container, let `live`
be the derived states of its children excluding `cancelled`:

| Condition on `live` | Derived state |
|---|---|
| empty | `cancelled` |
| any `blocked` | `blocked` |
| else any `in-progress` | `in-progress` |
| else all `done` | `done` |
| otherwise | `todo` |

A non-container task's derived state is simply its authored state.

### 10.3 Formulas

```
weighted_total = Σ weight(t)  for non-container t where state(t) ≠ cancelled
weighted_done  = Σ weight(t)  for non-container t where state(t) = done
percent        = round(100 × weighted_done / weighted_total)   (0 if total = 0)
```

`weight` defaults to `1`, so with uniform weights `weighted_*` equals the task
count. Whenever any task carries `weight ≠ 1`, renderings append ` pts` to
make the distinction visible. `tasks.live` in JSON counts tasks, not weight.

**Section rollups** apply the same formula to every non-container task in the
section's subtree. Because each task belongs to exactly one section, section
denominators sum to the file denominator (excluding unsectioned tasks, which
are reported as `top_progress`).

#### Worked example — `examples/auth-flow.tmda`

| | value |
|---|---|
| containers (excluded) | `T-003`, `T-010`, `T-016` |
| `weighted_total` | 14 (`T-001` has `weight=2`, `T-021` is cancelled and excluded) |
| `weighted_done` | 6 (`T-001`×2, `T-002`, `T-004`, `T-011`, `T-020`) |
| `percent` | `round(600/14)` = **43** |
| task counts | 5 done, 1 in progress, 1 blocked, 6 todo, 1 cancelled; `live` = 13 |

```
1. Research and design   4/5  (80%)   <- T-001(2) T-002 T-004 T-005
2. Implementation        1/7  (14%)   <- T-011..T-015, T-017, T-018
3. Verification          1/2  (50%)   <- T-020, T-022  (T-021 cancelled)
```

### 10.4 Derived file status

Evaluated top to bottom, first match wins:

| Condition | Derived status |
|---|---|
| `weighted_total = 0` | `planned` |
| `percent = 100` | `done` |
| any task `done` or `in-progress` | `active` |
| any task `blocked` | `blocked` |
| otherwise | `planned` |

Note that `active` outranks `blocked`: a file that has started and has one
blocker is active *and* has blockers. Agents should read
`progress.tasks.blocked` (or the `blocked` list) rather than relying on the
status string to surface individual blockers.

### 10.5 Eligible next tasks

The *next up* list is, in file then line order, the non-container tasks whose
state is `todo` or `in-progress` and whose `@blocked-by` references are all
`done` (or absent). The *blocked* list is every non-container task whose
derived state is `blocked`.

---

## 11. Progress region

The only place progress is written down. It is delimited by two exact HTML
comment strings:

```
<!-- tmda:progress:start -->
…generated content…
<!-- tmda:progress:end -->
```

| Rule | Severity |
|---|---|
| At most one region per file | `E041` |
| Every `start` has a matching `end`, and counts are equal | `E041` |
| If present, content MUST byte-match what `tmda sync` would generate | `E040` |
| Absent entirely | `W060` |

Progress MUST NOT be written anywhere else. There is no `percent:` front-matter
key by design: a second, unverifiable source of truth is exactly the failure
mode this format exists to prevent.

### 11.1 Rendering

Empty ledger:

```
**Progress** - no tasks yet
```

Otherwise:

```
**Progress** `{bar}` **{weighted_done}/{weighted_total}[ pts] ({percent}%)**[ - {counts}]
```

- `{bar}` is exactly 20 cells of `█` (filled) and `░` (empty); filled =
  `int(20 × weighted_done / weighted_total + 0.5)`.
- The ` pts` suffix appears only when some task has `weight ≠ 1`.
- `{counts}` is ` - `-joined, in this order, omitting zero values:
  `n in progress`, `n blocked`, `n todo`, `n cancelled`. Completed work is
  already expressed by the ratio and is not repeated.

Example:

```markdown
<!-- tmda:progress:start -->
**Progress** `█████████░░░░░░░░░░░` **6/14 pts (43%)** - 1 in progress - 1 blocked - 6 todo - 1 cancelled
<!-- tmda:progress:end -->
```

### 11.2 Placement

When `tmda sync` inserts a missing region it places it:

1. after the front matter;
2. after a single leading H1, any blank lines, and any immediately following
   blockquote lines (i.e. after the preamble);
3. padded with exactly one blank line before and after, unless it is already
   adjacent to the start or end of the file.

`tmda sync` is idempotent: re-running it on an up-to-date file changes nothing.

---

## 12. Detail links

The link from a ledger to its brief is the point of the format, and it is
validated rather than assumed.

| Where | Syntax | Checked |
|---|---|---|
| Front matter `detail` | `./brief.md` or `./brief.md#anchor` | always (required key) |
| Task metadata | `@detail=./brief.md#anchor` | when present |
| Front matter `depends_on` | file id or path | across the project |

**Resolution.** A relative reference resolves against the directory containing
the `.tmda` file. Absolute paths are accepted.

**Anchors.** If a `#fragment` is present, the target MUST contain a heading
whose GitHub-style slug equals the fragment, or an `<a name=…>`/`<a id=…>`
target. Slugs are derived by: stripping inline code, link markup and
emphasis; lowercasing; removing characters outside `[\w\s-]`; converting
whitespace runs to `-`. Setext headings and HTML `<h*>` elements are not
scanned — use ATX headings.

**Link back.** The brief SHOULD contain the ledger's filename somewhere, so a
human reading the prose can find the checkboxes. A brief that does not link
back warns (`W066`).

A missing target is `E008`; a missing anchor is `E009`. An unresolved
`depends_on` is `E051`; two files sharing an `id` is `E050`.

---

## 13. Validation

`tmda check` reports everything below. Errors make it exit `1`; `--strict`
promotes warnings to errors.

### 13.1 Errors

| Code | Condition |
|---|---|
| `E001` | Front matter absent |
| `E002` | Front matter is not valid YAML / not a mapping |
| `E003` | `tmda` missing, not an integer, or not version `1` |
| `E004` | `id` missing |
| `E005` | `id` is not a lowercase slug |
| `E006` | `title` missing |
| `E007` | `detail` missing or not a string |
| `E008` | Link target does not exist |
| `E009` | Link anchor not found in the target |
| `E010` | `status` not one of the five allowed values |
| `E011` | `updated` is not an ISO date |
| `E012` | `owner` is not a string |
| `E013` | `depends_on` is neither a string nor a list |
| `E020` | Tab used in a task line |
| `E021` | Unknown task state marker |
| `E022` | Task missing its id, or missing its description text |
| `E023` | Duplicate task id within the file |
| `E024` | Task id malformed (wrong case or fewer than three digits) |
| `E031` | `@weight` is not a positive integer |
| `E032` | `@due` is not an ISO date |
| `E033` | `@blocked-by` names a task id not in this file |
| `E034` | Duplicate metadata key on one task line |
| `E040` | Progress region is stale — run `tmda sync` |
| `E041` | Progress markers malformed, unterminated, or duplicated |
| `E050` | Two files in the project share a front-matter `id` |
| `E051` | `depends_on` target not found |

### 13.2 Warnings

| Code | Condition |
|---|---|
| `W060` | No progress region — run `tmda sync` |
| `W061` | Authored `status` contradicts derived status |
| `W062` | Container marked `[x]` while children are outstanding |
| `W063` | Indented task has no parent at a lower indent |
| `W064` | File contains no tasks |
| `W065` | H1 differs from front-matter `title` |
| `W066` | Brief does not link back to the ledger |
| `W068` | Unknown front-matter key |
| `W069` | Unknown task metadata key |

---

## 14. Tooling contract

Reference implementation: `tmda.py`, Python ≥ 3.9, standard library only
(PyYAML is used opportunistically if present).

### 14.1 Commands

| Command | Purpose |
|---|---|
| `tmda init NAME [--title --id --detail --owner --section --no-brief --force]` | Scaffold a ledger plus its brief stub, then insert a correct progress region |
| `tmda check [PATH…] [--strict] [--json]` | Validate. Exit `1` on errors (`--strict`: also warnings) |
| `tmda status [PATH…] [--no-tasks --totals-only --limit N --json]` | Human-readable progress report, or the machine report |
| `tmda sync [PATH…] [-v]` | Rewrite the progress region in place. Idempotent |
| `tmda list [PATH…] [--state --open --owner --blocked --id --limit N --json]` | Flat, filterable task list |
| `tmda mark TASK_ID STATE [PATH…] [--file --no-sync]` | Change a task's state by id and refresh progress |

`PATH` accepts files or directories; directories are searched recursively for
`*.tmda`, skipping dot-directories. Omitted paths default to `.`.

`STATE` accepts `todo`, `in-progress`, `done`, `blocked`, `cancelled` and the
usual aliases (`wip`, `doing`, `complete`, `canceled`, `skip`, …).

`--limit N` caps how many files (`status`) or tasks (`list`) are printed, and
prints a `showing N of M` header when it truncates. It is a display cap only:
counts, JSON totals and the project rollup still cover every match, so a
limited run can never read as a complete answer that happens to be short.
`status --totals-only` skips the per-file blocks entirely and prints just the
project rollup — the cheap way to ask "where are we" on a large repository.

Task ids are unique **within one file**, not across a project. Both `mark` and
`list --id` therefore refuse (exit `2`) when an id matches more than one file,
rather than return a plausible-looking wrong row; narrow with `PATH` or
`--file`.

`status` is a report, not a validation run: it exits `0` even for a ledger
`check` rejects, but it always says so — a file whose progress block no longer
matches its checkboxes is called out as *stale* with the `tmda sync` remedy,
and `status --json` counts those files in `stale`.

### 14.2 Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Validation errors (or warnings under `--strict`) |
| `2` | Usage error: unknown path, unknown state, ambiguous or missing task id, refusal to overwrite |

### 14.3 JSON reports

`check --json` and `status --json` share one file object:

```jsonc
{
  "spec": 1,
  "files": [{
    "path": "examples/auth-flow.tmda",
    "id": "auth-flow",
    "title": "Sign-in and session overhaul",
    "detail": "./auth-flow-brief.md",
    "owner": "joshua",
    "authored_status": "active",     // null when absent
    "status": "active",              // derived (§10.4)
    "archived": false,
    "progress": {
      "percent": 43,
      "weighted": { "done": 6, "total": 14 },
      "tasks": { "done": 5, "in_progress": 1, "blocked": 1,
                 "todo": 6, "cancelled": 1, "live": 13 },
      "weighted_points": true        // any weight != 1
    },
    "top_progress": null,            // present only if unsectioned tasks exist
    "sections": [                    // tree flattened; parent by line number
      { "title": "1. Research and design", "level": 2, "line": 21,
        "parent_line": null, "progress": { /* same shape */ } }
    ],
    "tasks": [{
      "id": "T-001",
      "state": "done",               // derived (container-aware)
      "authored_state": "done",
      "container": false,
      "text": "Audit the current login flow",
      "line": 22, "indent": 0,
      "parent": null,                // parent's id
      "section": "1. Research and design",
      "detail": "./auth-flow-brief.md#what-we-want-to-do",
      "owner": "joshua",
      "blocked_by": [],
      "due": null,
      "weight": 2
    }],
    "next":  ["T-005", "T-012"],     // eligible tasks (§10.5)
    "blocked": ["T-014"],
    "errors": [], "warnings": []     // pre-rendered diagnostic strings
  }],
  // check only:
  "cross_file_errors": [],
  "errors": 0,
  "warnings": 0,
  // status only:
  "count": 157,                // files matched, before --limit
  "shown": 157,                // files present in `files` after --limit
  "totals": { /* progress shape, aggregated over non-archived files */ },
  "totals_status": "active",
  "stale": 0                   // files whose progress block is out of date (E040)
}
```

`--limit` truncates `files` only. `count` against `shown` makes that visible,
and `totals`, `totals_status`, `errors`, `warnings` and `stale` keep covering
every match — so summing `files[].errors` equals the top-level `errors` only
when nothing was capped.

`list --json` emits `{ "spec": 1, "count": n, "shown": n, "tasks": [ …task
objects with "path" and "file_id" added… ] }`. `count` is how many tasks
matched the filters, `shown` is how many are in `tasks` after `--limit`, and
`tasks` never exceeds `shown`.

**Stability:** `spec`, the `progress` shape, `tasks[].id`, `tasks[].state`,
`next`, `blocked`, `errors` and `warnings` are the contract agents should
rely on. New keys may be added within spec version `1`; existing keys will not
change meaning.

### 14.4 How an agent should use this

1. `tmda status --json <project>` — get derived status, progress, `blocked`,
   `next` for every ledger.

   **Scope before you read.** The full report is every ledger's every task: on
   a mid-sized monorepo that is tens of thousands of tokens (and `status
   --json` runs to megabytes), which no agent should spend on a question about
   one service. Use `status --json --totals-only` for the project rollup, or
   `status --json <dir>` for the part you own; `--limit N` caps it, and
   `list --json` reports `count` against `shown` so a capped answer can never
   be mistaken for a complete one.
2. Read the `detail` file for any ledger it is about to touch. The checkboxes
   say *what*; the brief says *why*, and *why* decides how to do the work.
3. Pick from `next` (already filtered for unmet `@blocked-by`).
4. Do the work, then `tmda mark T-014 done` — never hand-edit a checkbox if
   the id can be given instead, and never hand-edit the progress block. If
   `status` reports the file as *stale*, the block was edited anyway: `tmda
   sync` repairs it, `check` would have refused it.
5. `tmda check` before handing back. Exit `0` means the ledgers are
   self-consistent and the numbers can be trusted.

---

## 15. Authoring recipe

1. **Write the brief first.** `brief.md` describes the outcome, why it
   matters, constraints, and what "done" looks like. Use ATX headings for the
   major design decisions — they become link targets.
2. **`tmda init my-ledger`** creates `my-ledger.tmda` with the front matter,
   a stub `my-ledger-brief.md` if none exists, and a correct progress region.
   Point `--detail` at an existing brief instead if you already wrote it.
3. **Split the brief into sections** with `##` headings that mirror its
   structure.
4. **Write tasks as leaves**, one outcome per line, each with an id. Nest
   sub-steps under a container when they are genuinely sub-steps — the
   container then reports itself.
5. **Link tasks into the prose** with `@detail=./brief.md#heading` so any
   task can be traced to its rationale in one hop.
6. **Record blockers explicitly** with `[!]` and `@blocked-by=T-…`. Do not
   leave a blocked task as `[ ]` — nothing will surface it.
7. **Use `@weight` sparingly**, for genuinely larger pieces of work. Weights
   are points, not estimates.
8. **Run `tmda check --strict` in CI.** A stale progress block fails the
   build; that is the point.

---

## 16. Versioning and future work

The `tmda` front-matter key carries the spec version. This document is
version `1`; a tool MUST refuse a file declaring an unknown version (`E003`)
rather than guess.

Candidate additions for a future version, all backwards-compatible in spirit:

- `@blocked-by` across files (`auth-flow:T-014`) — currently file-scoped.
- Recurring and checklist-template tasks.
- A `depends-on` edge type finer than the whole-file `depends_on`.
- Optional task comments / evidence links that `check` can verify.
- Setext heading and HTML-anchor scanning for link validation.

