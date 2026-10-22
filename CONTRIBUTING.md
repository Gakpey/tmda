# Contributing to tmda

Thanks for helping. This project is small on purpose and the bar for changes
is correspondingly high: the `.tmda` format is something other people's
repositories will depend on.

## Ground rules

1. **Standard library only** at runtime. `tmda.py` must keep working on a
   bare Python install. PyYAML is used *opportunistically* if it happens to be
   present, never required.
2. **One file.** `tmda.py` is the whole tool. Splitting it is a bigger
   conversation than a typical PR.
3. **The format is versioned.** Anything that changes what a valid `.tmda`
   file looks like does not land in spec version `1`. Propose it in an issue
   first (see `SPEC.md` §16).
4. **Progress stays derived.** Do not add a way to author progress by hand.
   The absence of that key is the point of the format.

## Development setup

```console
$ git clone https://github.com/Gakpey/tmda.git
$ cd tmda
$ python3 -m venv .venv && . .venv/bin/activate
$ pip install -e ".[dev,yaml]"
```

## Running the checks

Everything CI runs, locally:

```console
$ python3 -m unittest discover -s tests -v     # 68 tests
$ ruff check .                                 # lint
$ tmda check --strict examples                 # dogfood the shipped examples
$ python3 -m build && twine check dist/*       # packaging
```

The test suite runs twice in CI: once **without** PyYAML (the built-in YAML
subset) and once **with** it. Please keep it passing in both configurations -
if you touch front-matter parsing, check `test_works_without_pyyaml`.

## Tests

- Every bug fix gets a regression test that fails without the fix.
- Every new validation rule gets a test asserting both the error code and
  that valid input does *not* produce it.
- CLI behaviour is tested through `tmda.main()` so exit codes are covered.

## Documentation

Behaviour lives in three places and they move together:

| File | What must stay in sync |
|---|---|
| `SPEC.md` | Grammar, error/warning code tables, JSON shapes |
| `README.md` | Commands, quickstart, examples |
| `CHANGELOG.md` | What changed for users |

If you add or rename a diagnostic code, update the table in `SPEC.md` §13 in
the same commit.

## Pull requests

Use the checklist in the PR template. Small, focused PRs are much easier to
review than a combined refactor + feature.

## Releasing (maintainers)

Version lives in `pyproject.toml` and must match the tag.

1. Move `[Unreleased]` entries in `CHANGELOG.md` under a new
   `## [X.Y.Z] - YYYY-MM-DD` heading, and update the link references at the
   bottom.
2. Set `version = "X.Y.Z"` in `pyproject.toml`.
3. Commit, then tag and push:

   ```console
   $ git commit -am "Release vX.Y.Z"
   $ git tag -a vX.Y.Z -m "vX.Y.Z"
   $ git push origin main vX.Y.Z
   ```

4. The `Release` workflow builds the sdist/wheel, verifies the tag matches
   `pyproject.toml`, publishes to PyPI via **Trusted Publishing**, opens a
   GitHub Release carrying the changelog entry as its notes, and repoints the
   floating `vMAJOR` tag (what `uses: Gakpey/tmda@v1` resolves to).

   The trigger is `v*.*.*`, so `v1` itself never re-enters the workflow. The
   major tag is moved with `GITHUB_TOKEN`, which GitHub does not feed back
   into workflow triggers.

PyPI Trusted Publishing is a one-time setup: on pypi.org add a *pending
publisher* for this repository (owner `Gakpey`, repo `tmda`, workflow
`publish.yml`, environment `pypi`), then create a GitHub environment named
`pypi` in the repository settings. No API tokens are stored.

To publish a first release before any tag exists, install it manually once:

```console
$ python3 -m build && python3 -m twine upload dist/*
```

(or let the workflow do it from `v1.0.0` onward).

## Reporting security issues

Please do not open a public issue. Use GitHub's private vulnerability
reporting for this repository.
