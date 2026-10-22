"""Unit tests for the tmda CLI (stdlib unittest, no external deps).

Run from the tmda/ directory:

    python3 -m unittest discover -s tests -v
"""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tmda  # noqa: E402


BRIEF = """# Demo - Brief

Task file: [demo.tmda](./demo.tmda)

## What we want to do

Ship the thing.

## Design notes

### Deep dive

Details here.
"""


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def scaffold(root: Path, body: str, front: str = None, brief: str = BRIEF) -> Path:
    """Write demo.tmda (plus brief) under root and return the task file."""
    if brief is not None:
        write(root / "demo-brief.md", brief)
    if front is None:
        front = (
            "---\n"
            "tmda: 1\n"
            "id: demo\n"
            "title: Demo\n"
            "detail: ./demo-brief.md\n"
            "status: planned\n"
            "---\n\n"
            "# Demo\n\n"
        )
    return write(root / "demo.tmda", front + body)


def codes(report) -> list:
    return sorted({i.code for i in report.issues if i.severity == "error"})


def warn_codes(report) -> list:
    return sorted({i.code for i in report.issues if i.severity == "warning"})


def run(argv) -> tuple:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = tmda.main(argv)
    return code, out.getvalue()


class TempCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="tmda-test-")
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)


# ---------------------------------------------------------------- meta / ids


class TestLineGrammar(unittest.TestCase):
    def test_meta_split_unquoted(self):
        meta, text, dupes = tmda.parse_meta(
            "Pick the rotation interval @owner=joshua @due=2026-10-28")
        self.assertEqual(text, "Pick the rotation interval")
        self.assertEqual(meta, {"owner": "joshua", "due": "2026-10-28"})
        self.assertEqual(dupes, [])

    def test_meta_split_quoted_value_may_contain_spaces(self):
        meta, text, _ = tmda.parse_meta(
            'See the brief @detail="./my brief.md#anchor"')
        self.assertEqual(meta["detail"], "./my brief.md#anchor")
        self.assertEqual(text, "See the brief")

    def test_duplicate_meta_reported_right_to_left(self):
        meta, _, dupes = tmda.parse_meta("Do it @owner=a @owner=b")
        self.assertEqual(dupes, ["owner"])
        self.assertEqual(meta["owner"], "b")   # rightmost wins, one reported

    def test_prose_without_meta_is_untouched(self):
        meta, text, _ = tmda.parse_meta("Just some text about @signatures")
        self.assertEqual(meta, {})
        self.assertEqual(text, "Just some text about @signatures")

    def test_id_forms(self):
        self.assertEqual(tmda.ID_RE.match("T-001 Ship it").group(2), "Ship it")
        self.assertIsNone(tmda.ID_RE.match("SHIP-1 nope"))
        self.assertIsNone(tmda.ID_RE.match("T-1 too short"))

    def test_slugify(self):
        self.assertEqual(tmda.slugify("What we want to do"), "what-we-want-to-do")
        self.assertEqual(tmda.slugify("Design notes (`v2`)"), "design-notes-v2")


# ------------------------------------------------------------------ parsing


class TestParsing(TempCase):
    def test_missing_front_matter(self):
        p = write(self.root / "demo.tmda", "- [ ] T-001 Hi\n")
        rep = tmda.analyze(p)
        self.assertIn("E001", codes(rep))

    def test_required_front_matter_keys(self):
        p = scaffold(self.root, "- [ ] T-001 Hi\n",
                     front="---\ntmda: 1\n---\n\n# Demo\n\n")
        rep = tmda.analyze(p)
        for code in ("E004", "E006", "E007"):
            self.assertIn(code, codes(rep))

    def test_unsupported_spec_version(self):
        p = scaffold(self.root, "- [ ] T-001 Hi\n",
                     front="---\ntmda: 99\nid: demo\ntitle: Demo\n"
                           "detail: ./demo-brief.md\n---\n\n# Demo\n\n")
        self.assertIn("E003", codes(tmda.analyze(p)))

    def test_missing_task_id(self):
        p = scaffold(self.root, "- [ ] Ship the thing\n")
        self.assertIn("E022", codes(tmda.analyze(p)))

    def test_lowercase_task_id(self):
        p = scaffold(self.root, "- [ ] t-001 Ship the thing\n")
        self.assertIn("E024", codes(tmda.analyze(p)))

    def test_duplicate_task_id(self):
        p = scaffold(self.root, "- [ ] T-001 One\n- [ ] T-001 Two\n")
        self.assertIn("E023", codes(tmda.analyze(p)))

    def test_unknown_state_marker(self):
        p = scaffold(self.root, "- [?] T-001 Weird\n")
        self.assertIn("E021", codes(tmda.analyze(p)))

    def test_tab_indentation_rejected(self):
        p = scaffold(self.root, "- [ ] T-001 Root\n\t- [ ] T-002 Child\n")
        self.assertIn("E020", codes(tmda.analyze(p)))

    def test_plain_bullet_is_not_a_task(self):
        p = scaffold(self.root, "- [note] just a note\n- [ ] T-001 Real\n")
        rep = tmda.analyze(p)
        self.assertEqual(len(rep.tasks), 1)
        self.assertEqual(codes(rep), [])

    def test_headings_inside_fences_are_ignored(self):
        body = "```yaml\n## not a heading\n```\n- [ ] T-001 Real\n"
        p = scaffold(self.root, body)
        rep = tmda.analyze(p)
        self.assertEqual(rep.sections, [])
        self.assertEqual(len(rep.tasks), 1)

    def test_unknown_metadata_is_a_warning(self):
        p = scaffold(self.root, "- [ ] T-001 Thing @colour=red\n")
        rep = tmda.analyze(p)
        self.assertEqual(codes(rep), [])
        self.assertIn("W069", warn_codes(rep))

    def test_bad_weight_and_due(self):
        p = scaffold(self.root,
                     "- [ ] T-001 A @weight=0\n"
                     "- [ ] T-002 B @due=not-a-date\n")
        for code in ("E031", "E032"):
            self.assertIn(code, codes(tmda.analyze(p)))

    def test_duplicate_metadata_key(self):
        p = scaffold(self.root, "- [ ] T-001 A @owner=x @owner=y\n")
        self.assertIn("E034", codes(tmda.analyze(p)))


# --------------------------------------------------------------- front matter


class TestFrontMatter(unittest.TestCase):
    def test_mini_yaml_scalars(self):
        data = tmda.mini_yaml(
            "tmda: 1\nid: demo\ntitle: Hello\ndetail: ./b.md\n"
            "status: active\nupdated: 2026-10-22\n")
        self.assertEqual(data["tmda"], 1)
        self.assertEqual(data["title"], "Hello")
        self.assertEqual(data["updated"], "2026-10-22")

    def test_mini_yaml_lists(self):
        data = tmda.mini_yaml(
            "tags: [auth, security]\ndepends_on:\n  - one\n  - two\n")
        self.assertEqual(data["tags"], ["auth", "security"])
        self.assertEqual(data["depends_on"], ["one", "two"])

    def test_mini_yaml_rejects_nested_maps(self):
        with self.assertRaises(ValueError):
            tmda.mini_yaml("outer:\n  inner: 1\n")

    def test_bad_status_value(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = scaffold(root, "- [ ] T-001 A\n",
                         front="---\ntmda: 1\nid: demo\ntitle: Demo\n"
                               "detail: ./demo-brief.md\nstatus: nope\n"
                               "---\n\n# Demo\n\n")
            self.assertIn("E010", codes(tmda.analyze(p)))

    def test_bad_updated_date(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = scaffold(root, "- [ ] T-001 A\n",
                         front="---\ntmda: 1\nid: demo\ntitle: Demo\n"
                               "detail: ./demo-brief.md\nupdated: Friday\n"
                               "---\n\n# Demo\n\n")
            self.assertIn("E011", codes(tmda.analyze(p)))

    def test_works_without_pyyaml(self):
        """Absence of PyYAML must change nothing: same front matter, same
        diagnostics. It must NOT warn, or `--strict` (used by the reusable
        GitHub Action) would fail on a default install.
        """
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = scaffold(root, "- [ ] T-001 A\n")
            with_yaml = tmda.analyze(p)     # PyYAML if installed, subset if not

            sentinel = object()
            saved = sys.modules.get("yaml", sentinel)
            sys.modules["yaml"] = None     # makes `import yaml` raise
            try:
                rep = tmda.analyze(p)
            finally:
                if saved is sentinel:
                    sys.modules.pop("yaml", None)
                else:
                    sys.modules["yaml"] = saved

            self.assertEqual(rep.front, with_yaml.front)
            self.assertEqual(rep.issues, with_yaml.issues)
            self.assertEqual(codes(rep), [])
            self.assertEqual(warn_codes(rep), ["W060"])  # only "no progress block"

    def test_out_of_subset_yaml_still_errors_without_pyyaml(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = scaffold(root, "- [ ] T-001 A\n",
                         front="---\ntmda: 1\nid: demo\ntitle: Demo\n"
                               "detail: ./demo-brief.md\nouter:\n  inner: 1\n"
                               "---\n\n# Demo\n\n")
            sentinel = object()
            saved = sys.modules.get("yaml", sentinel)
            sys.modules["yaml"] = None
            try:
                rep = tmda.analyze(p)
            finally:
                if saved is sentinel:
                    sys.modules.pop("yaml", None)
                else:
                    sys.modules["yaml"] = saved
            self.assertIn("E002", codes(rep))
            message = next(i.message for i in rep.issues if i.code == "E002")
            self.assertIn("tmda[yaml]", message)


# ----------------------------------------------------------------- progress


class TestProgress(TempCase):
    def test_containers_are_derived_and_excluded(self):
        p = scaffold(self.root, (
            "## Section\n"
            "- [ ] T-001 Parent\n"
            "  - [x] T-002 Child one\n"
            "  - [x] T-003 Child two\n"))
        rep = tmda.analyze(p)
        parent = rep.tasks[0]
        self.assertTrue(parent.container)
        self.assertEqual(parent.derived, "done")
        # only the two children count: 2/2
        self.assertEqual(rep.progress["percent"], 100)
        self.assertEqual(rep.progress["weighted"], {"done": 2, "total": 2})

    def test_container_with_partial_children(self):
        p = scaffold(self.root, (
            "- [x] T-001 Parent\n"
            "  - [x] T-002 Child\n"
            "  - [ ] T-003 Child\n"))
        rep = tmda.analyze(p)
        self.assertEqual(rep.tasks[0].derived, "todo")
        self.assertIn("W062", warn_codes(rep))
        self.assertEqual(rep.progress["percent"], 50)

    def test_cancelled_excluded_from_denominator(self):
        p = scaffold(self.root,
                     "- [x] T-001 A\n- [-] T-002 B\n- [ ] T-003 C\n")
        rep = tmda.analyze(p)
        self.assertEqual(rep.progress["weighted"], {"done": 1, "total": 2})
        self.assertEqual(rep.progress["tasks"]["cancelled"], 1)
        self.assertEqual(rep.progress["percent"], 50)

    def test_weights(self):
        p = scaffold(self.root,
                     "- [x] T-001 A @weight=3\n- [ ] T-002 B\n")
        rep = tmda.analyze(p)
        self.assertEqual(rep.progress["weighted"], {"done": 3, "total": 4})
        self.assertEqual(rep.progress["percent"], 75)
        self.assertTrue(rep.progress["weighted_points"])

    def test_blocked_derivation_propagates_to_container(self):
        p = scaffold(self.root, (
            "- [ ] T-001 Parent\n"
            "  - [!] T-002 Child\n"))
        rep = tmda.analyze(p)
        self.assertEqual(rep.tasks[0].derived, "blocked")
        self.assertEqual([t.id for t in rep.blocked()], ["T-002"])

    def test_derived_statuses(self):
        cases = [
            ("- [ ] T-001 A\n", "planned"),
            ("- [~] T-001 A\n", "active"),
            ("- [x] T-001 A\n", "done"),
            ("- [!] T-001 A\n", "blocked"),
            ("- [-] T-001 A\n", "planned"),
        ]
        for body, expected in cases:
            with self.subTest(body=body):
                rep = tmda.analyze(scaffold(self.root, body))
                self.assertEqual(rep.status, expected)

    def test_next_up_respects_blockers(self):
        p = scaffold(self.root, (
            "- [ ] T-001 Blocked @blocked-by=T-002\n"
            "- [ ] T-002 Available\n"))
        rep = tmda.analyze(p)
        self.assertEqual([t.id for t in rep.next_up()], ["T-002"])

    def test_unknown_blocked_by_reference(self):
        p = scaffold(self.root, "- [ ] T-001 A @blocked-by=T-999\n")
        self.assertIn("E033", codes(tmda.analyze(p)))

    def test_section_rollup(self):
        p = scaffold(self.root, (
            "## One\n"
            "- [x] T-001 A\n"
            "- [ ] T-002 B\n"
            "## Two\n"
            "- [x] T-003 C\n"
            "### Two point one\n"
            "- [x] T-004 D\n"))
        rep = tmda.analyze(p)
        self.assertEqual(len(rep.sections), 3)
        self.assertEqual(rep.section_progress[0]["percent"], 50)
        self.assertEqual(rep.section_progress[1]["percent"], 100)
        self.assertEqual(rep.section_progress[2]["percent"], 100)

    def test_unsectioned_tasks_roll_up_to_file(self):
        p = scaffold(self.root, "- [x] T-001 A\n- [ ] T-002 B\n## S\n- [x] T-003 C\n")
        rep = tmda.analyze(p)
        self.assertEqual(len(rep.unsectioned), 2)
        self.assertEqual(rep.top_progress["percent"], 50)
        self.assertEqual(rep.progress["percent"], round(100 * 2 / 3))


# ------------------------------------------------------------- links / sync


class TestLinks(TempCase):
    def test_missing_brief(self):
        p = scaffold(self.root, "- [ ] T-001 A\n", brief=None)
        self.assertIn("E008", codes(tmda.analyze(p)))

    def test_valid_anchor(self):
        p = scaffold(self.root,
                     "- [ ] T-001 A @detail=./demo-brief.md#deep-dive\n")
        self.assertEqual(codes(tmda.analyze(p)), [])

    def test_missing_anchor(self):
        p = scaffold(self.root,
                     "- [ ] T-001 A @detail=./demo-brief.md#nope\n")
        self.assertIn("E009", codes(tmda.analyze(p)))

    def test_brief_must_link_back(self):
        p = scaffold(self.root, "- [ ] T-001 A\n",
                     brief="# Brief\n\nNo link here.\n")
        self.assertIn("W066", warn_codes(tmda.analyze(p)))

    def test_slug_matches_headings(self):
        scaffold(self.root, "- [ ] T-001 A\n")
        anchors = tmda.collect_anchors(self.root / "demo-brief.md")
        self.assertIn("what-we-want-to-do", anchors)
        self.assertIn("deep-dive", anchors)


class TestSync(TempCase):
    def test_insert_then_idempotent(self):
        p = scaffold(self.root, "## S\n- [x] T-001 A\n- [ ] T-002 B\n")
        rep = tmda.analyze(p)
        self.assertFalse(rep.has_progress)
        self.assertIn("W060", warn_codes(rep))

        text, note = tmda.sync_text(rep.text, rep.progress)
        self.assertEqual(note, "inserted")
        write(p, text)

        rep2 = tmda.analyze(p)
        self.assertTrue(rep2.has_progress)
        self.assertEqual(codes(rep2), [])
        self.assertEqual(warn_codes(rep2), [])

        text3, note3 = tmda.sync_text(rep2.text, rep2.progress)
        self.assertEqual(note3, "unchanged")
        self.assertEqual(text3, rep2.text)

    def test_stale_block_detected(self):
        p = scaffold(self.root, "## S\n- [x] T-001 A\n- [ ] T-002 B\n")
        rep = tmda.analyze(p)
        text, _ = tmda.sync_text(rep.text, rep.progress)
        write(p, text)
        # now change a task without syncing
        write(p, text.replace("- [ ] T-002 B", "- [x] T-002 B"))
        rep2 = tmda.analyze(p)
        self.assertTrue(rep2.stale_progress)
        self.assertIn("E040", codes(rep2))

        text2, note = tmda.sync_text(rep2.text, rep2.progress)
        self.assertEqual(note, "updated")
        write(p, text2)
        self.assertEqual(codes(tmda.analyze(p)), [])

    def test_unterminated_markers(self):
        p = scaffold(self.root, "- [ ] T-001 A\n<!-- tmda:progress:start -->\n")
        self.assertIn("E041", codes(tmda.analyze(p)))

    def test_empty_progress_block(self):
        p = scaffold(self.root, "")
        rep = tmda.analyze(p)
        text, note = tmda.sync_text(rep.text, rep.progress)
        self.assertEqual(note, "inserted")
        write(p, text)
        rep2 = tmda.analyze(p)
        self.assertEqual(codes(rep2), [])
        self.assertIn("no tasks yet", rep2.text)

    def test_sync_inserts_after_preamble(self):
        p = scaffold(self.root, "## S\n- [ ] T-001 A\n")
        rep = tmda.analyze(p)
        text, _ = tmda.sync_text(rep.text, rep.progress)
        self.assertLess(text.index(tmda.PROG_START), text.index("## S"))
        self.assertGreater(text.index(tmda.PROG_START), text.index("# Demo"))

    def test_block_is_padded_with_exactly_one_blank_line(self):
        for body in ("## S\n- [ ] T-001 A\n",
                     "- [ ] T-001 A\n",
                     ""):
            with self.subTest(body=body):
                p = scaffold(self.root, body)
                rep = tmda.analyze(p)
                text, _ = tmda.sync_text(rep.text, rep.progress)
                after = text[text.index(tmda.PROG_END) + len(tmda.PROG_END):]
                before = text[:text.index(tmda.PROG_START)]
                self.assertFalse(after.startswith("\n\n\n"), repr(after[:8]))
                self.assertTrue(before.endswith("\n\n"), repr(before[-8:]))
                self.assertFalse(before.endswith("\n\n\n"), repr(before[-8:]))
                if after:
                    self.assertTrue(after.startswith("\n\n"), repr(after[:8]))
                else:
                    self.assertTrue(text.endswith("\n"))


# -------------------------------------------------------------------- CLI


class TestCli(TempCase):
    def test_init_creates_file_and_brief(self):
        code, out = run(["init", str(self.root / "my-thing"),
                         "--title", "My Thing"])
        self.assertEqual(code, 0, out)
        f = self.root / "my-thing.tmda"
        b = self.root / "my-thing-brief.md"
        self.assertTrue(f.exists())
        self.assertTrue(b.exists())
        content = f.read_text()
        self.assertIn("id: my-thing", content)
        self.assertIn("detail: ./my-thing-brief.md", content)
        self.assertNotIn("././", content)
        self.assertIn(tmda.PROG_START, content)
        code, out = run(["check", str(f)])
        self.assertEqual(code, 0, out)

    def test_init_refuses_to_overwrite(self):
        run(["init", str(self.root / "x")])
        code, _ = run(["init", str(self.root / "x")])
        self.assertEqual(code, 2)

    def test_init_no_brief_missing_target_fails(self):
        code, out = run(["init", str(self.root / "nb"), "--no-brief"])
        self.assertEqual(code, 1)
        self.assertIn("E008", out)
        self.assertIn("does not resolve", out)

    def test_check_fails_on_errors(self):
        p = scaffold(self.root, "- [ ] no id here\n")
        code, out = run(["check", str(p)])
        self.assertEqual(code, 1)
        self.assertIn("E022", out)

    def test_check_passes_clean_file(self):
        p = scaffold(self.root, "- [x] T-001 A\n")
        rep = tmda.analyze(p)
        text, _ = tmda.sync_text(rep.text, rep.progress)
        write(p, text)
        code, out = run(["check", str(p)])
        self.assertEqual(code, 0, out)
        self.assertIn("OK", out)

    def test_check_strict_promotes_warnings(self):
        p = scaffold(self.root, "- [ ] T-001 A @colour=red\n")
        code, _ = run(["check", str(p)])
        self.assertEqual(code, 0)
        code, _ = run(["check", "--strict", str(p)])
        self.assertEqual(code, 1)

    def test_check_json(self):
        p = scaffold(self.root, "- [x] T-001 A\n- [ ] T-002 B\n")
        rep = tmda.analyze(p)
        text, _ = tmda.sync_text(rep.text, rep.progress)
        write(p, text)
        code, out = run(["check", "--json", str(p)])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["spec"], 1)
        self.assertEqual(payload["errors"], 0)
        self.assertEqual(payload["files"][0]["tasks"][0]["id"], "T-001")

    def test_status_json(self):
        p = scaffold(self.root, "- [x] T-001 A\n- [ ] T-002 B\n")
        code, out = run(["status", "--json", str(p)])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["totals"]["percent"], 50)
        self.assertEqual(payload["totals_status"], "active")
        self.assertEqual(payload["files"][0]["id"], "demo")

    def test_status_text(self):
        p = scaffold(self.root, "## S\n- [x] T-001 A\n- [!] T-002 B\n")
        code, out = run(["status", str(p)])
        self.assertEqual(code, 0)
        self.assertIn("blocked", out)
        self.assertIn("T-002", out)

    def test_json_parent_and_container(self):
        p = scaffold(self.root, "- [ ] T-001 P\n  - [ ] T-002 C\n")
        code, out = run(["status", "--json", str(p)])
        self.assertEqual(code, 0)
        tasks = {t["id"]: t
                 for t in json.loads(out)["files"][0]["tasks"]}
        self.assertTrue(tasks["T-001"]["container"])
        self.assertIsNone(tasks["T-001"]["parent"])
        self.assertEqual(tasks["T-002"]["parent"], "T-001")
        # containers are excluded from the denominator: 1 live task, 0 done
        self.assertEqual(json.loads(out)["files"][0]["progress"]["weighted"],
                         {"done": 0, "total": 1})

    def test_list_filters(self):
        p = scaffold(self.root,
                     "- [x] T-001 A\n- [ ] T-002 B\n- [!] T-003 C\n")
        code, out = run(["list", "--state", "done", str(p)])
        self.assertEqual(code, 0)
        self.assertIn("T-001", out)
        self.assertNotIn("T-002", out)

        code, out = run(["list", "--open", "--json", str(p)])
        payload = json.loads(out)
        self.assertEqual(payload["count"], 2)

    def test_list_rejects_unknown_state(self):
        p = scaffold(self.root, "- [ ] T-001 A\n")
        code, _ = run(["list", "--state", "sideways", str(p)])
        self.assertEqual(code, 2)

    def test_mark_roundtrip(self):
        p = scaffold(self.root, "- [ ] T-001 A\n- [ ] T-002 B\n")
        rep = tmda.analyze(p)
        text, _ = tmda.sync_text(rep.text, rep.progress)
        write(p, text)

        code, out = run(["mark", "T-001", "done", "--file", str(p)])
        self.assertEqual(code, 0, out)
        self.assertIn("[x]", p.read_text())

        code, out = run(["mark", "T-001", "blocked", "--file", str(p)])
        self.assertEqual(code, 0, out)
        self.assertIn("[!]", p.read_text())

        code, _ = run(["mark", "T-999", "done", "--file", str(p)])
        self.assertEqual(code, 2)

    def test_mark_ambiguity_across_files(self):
        d2 = self.root / "sub"
        d2.mkdir()
        p1 = scaffold(self.root, "- [ ] T-001 A\n")
        p2 = scaffold(d2, "- [ ] T-001 A\n")
        # no --file and two candidates -> refuse
        code, _ = run(["mark", "T-001", "done", str(self.root)])
        self.assertEqual(code, 2)
        # --file disambiguates
        self.assertEqual(run(["mark", "T-001", "done", "--file", str(p1)])[0], 0)
        self.assertEqual(run(["mark", "T-001", "done", "--file", str(p2)])[0], 0)
        self.assertIn("[x]", p1.read_text())
        self.assertIn("[x]", p2.read_text())

    def test_mark_reports_unknown_state(self):
        p = scaffold(self.root, "- [ ] T-001 A\n")
        code, _ = run(["mark", "T-001", "wobbly", "--file", str(p)])
        self.assertEqual(code, 2)

    def test_sync_command(self):
        p = scaffold(self.root, "- [ ] T-001 A\n")
        code, out = run(["sync", str(p)])
        self.assertEqual(code, 0)
        self.assertIn("inserted", out)
        code, out = run(["sync", str(p)])
        self.assertEqual(code, 0)
        self.assertIn("up to date", out)

    def test_duplicate_file_ids(self):
        d2 = self.root / "sub"
        d2.mkdir()
        p1 = scaffold(self.root, "- [ ] T-001 A\n")
        p2 = scaffold(d2, "- [ ] T-001 A\n")
        self.assertNotEqual(p1.resolve(), p2.resolve())
        code, out = run(["check", str(self.root)])
        self.assertEqual(code, 1)
        self.assertIn("E050", out)

    def test_depends_on_resolves_by_id(self):
        """The target ledger's *filename* differs from its id, so only the
        id table can satisfy depends_on (a path lookup cannot)."""
        d2 = self.root / "sub"
        d2.mkdir()

        def ledger(path, fid, deps=()):
            dep = "depends_on: [%s]\n" % ", ".join(deps) if deps else ""
            brief = path.with_name("brief.md")
            brief.write_text(
                f"# Brief\n\nTask file: [{path.name}](./{path.name})\n",
                encoding="utf-8")
            path.write_text(
                "---\n"
                f"tmda: 1\nid: {fid}\ntitle: {fid.title()}\n"
                "detail: ./brief.md\n"
                f"{dep}"
                "---\n\n"
                f"# {fid.title()}\n\n"
                "- [ ] T-001 A\n",
                encoding="utf-8")

        ledger(self.root / "auth.tmda", "auth")
        ledger(d2 / "other.tmda", "other", deps=["auth"])
        # a path lookup for "auth" inside sub/ cannot work; the id table must
        code, out = run(["check", str(self.root)])
        self.assertEqual(code, 0, out)

        # and a dangling id still fails
        ledger(d2 / "other.tmda", "other", deps=["ghost"])
        code, out = run(["check", str(self.root)])
        self.assertEqual(code, 1)
        self.assertIn("E051", out)

    def test_missing_depends_on(self):
        p = scaffold(self.root, "- [ ] T-001 A\n",
                     front="---\ntmda: 1\nid: demo\ntitle: Demo\n"
                           "detail: ./demo-brief.md\ndepends_on: [ghost]\n"
                           "---\n\n# Demo\n\n")
        code, out = run(["check", str(p)])
        self.assertEqual(code, 1)
        self.assertIn("E051", out)

    def test_examples_are_clean(self):
        examples = Path(__file__).resolve().parent.parent / "examples"
        if not examples.exists():
            self.skipTest("examples/ not present")
        code, out = run(["check", str(examples)])
        self.assertEqual(code, 0, out)


class TestDiscover(TempCase):
    def test_finds_nested_and_skips_hidden(self):
        (self.root / "a").mkdir()
        (self.root / ".hidden").mkdir()
        scaffold(self.root, "- [ ] T-001 A\n")
        scaffold(self.root / "a", "- [ ] T-001 A\n")
        scaffold(self.root / ".hidden", "- [ ] T-001 A\n")
        found = [str(p) for p in tmda.discover([str(self.root)])]
        self.assertEqual(len(found), 2)
        self.assertFalse(any(".hidden" in f for f in found))


if __name__ == "__main__":
    unittest.main()
