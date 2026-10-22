## What changed

<!-- A sentence or two. Link the issue with "Fixes #123" if there is one. -->

## Why

<!-- What was awkward or wrong before? For format changes, this is the part
     that gets the most scrutiny. -->

## How was it verified

<!-- e.g. "68 tests pass", "ran `tmda check --strict examples` on a file
     using weights and cancelled tasks" -->

## Checklist

- [ ] `python3 -m unittest discover -s tests` passes
- [ ] `ruff check .` passes
- [ ] `tmda check --strict examples` still passes
- [ ] `README.md` / `SPEC.md` updated if observable behaviour changed
- [ ] `CHANGELOG.md` has an entry under `[Unreleased]`
- [ ] New behaviour is covered by a test
- [ ] **Format change:** I have not altered what a spec-version-`1` file may
      look like (if I have, I have explained why it warrants a version bump
      in `SPEC.md`)
