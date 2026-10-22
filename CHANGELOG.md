# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Within spec version `1`, the `.tmda` **format** only changes when the `tmda:`
front-matter key changes. Additive CLI flags and JSON keys are minor releases;
removing or repurposing a documented key is a major release.

## [Unreleased]

## [1.0.0] - 2026-10-22

First release. Format spec version `1`.

### Added

- **`.tmda` format spec version 1** (`SPEC.md`) — a CommonMark + YAML
  front-matter profile for task ledgers: stable task ids (`T-001`), five
  checkbox states (`todo`, `in-progress`, `done`, `blocked`, `cancelled`),
  `@detail` / `@owner` / `@blocked-by` / `@weight` / `@due` metadata,
  heading-based sections, and container tasks whose state is derived from
  their children.
- **Derived progress** — weighted, cancellation-aware rollups computed from
  the checkboxes and rendered into a managed `<!-- tmda:progress -->` block.
  Progress is never authored; a stale block fails validation (`E040`).
- **Required brief link** — every ledger must declare `detail:`; targets and
  `#anchors` are validated, and briefs are warned (`W066`) if they do not link
  back to their ledger.
- **`tmda` CLI** (single file, standard library only, Python ≥ 3.9):
  - `tmda init` — scaffold a ledger and its linked brief
  - `tmda check` — validate syntax, ids, links and the progress block
    (`--strict`, `--json`)
  - `tmda status` — per-file, per-section and project progress (`--json`)
  - `tmda sync` — rewrite the progress block in place; idempotent
  - `tmda list` — flat task list with `--state`, `--open`, `--owner`,
    `--blocked`, `--id`
  - `tmda mark` — change a task's state by id and refresh progress
- **Validation rule set** — 25 error codes and 9 warning codes covering
  front matter, task grammar, metadata, links, progress and cross-file
  consistency (`SPEC.md` §13).
- **Machine contract** — stable JSON reports from `check --json`,
  `status --json` and `list --json`, plus documented exit codes
  (`0` ok, `1` validation errors, `2` usage error).
- **Examples** — two ledger/brief pairs showing nesting, weights, blockers,
  cancelled work and a cross-file `depends_on`.
- **Tests** — 67 unit and CLI tests (`python3 -m unittest discover -s tests`).

[Unreleased]: https://github.com/Gakpey/tmda/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/Gakpey/tmda/releases/tag/v1.0.0
