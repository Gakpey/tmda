#!/usr/bin/env python3
"""
tmda - Task Markdown for Agents (.tmda)

Stdlib-only CLI for authoring, validating and reporting on .tmda files.

Commands
--------
  init    scaffold a new .tmda file and its linked brief
  check   validate syntax, ids, links and the managed progress block
  status  human/agent readable progress report
  sync    rewrite the managed progress block in place
  list    flat, filterable task list (text or JSON)
  mark    change a task's state by id

See SPEC.md for the normative format specification.

Exit codes: 0 = ok, 1 = validation errors, 2 = usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

__version__ = "1.0.0"
SPEC_VERSION = 1
BAR_WIDTH = 20

# --------------------------------------------------------------------------
# vocabulary
# --------------------------------------------------------------------------

STATE_BY_MARK = {
    " ": "todo",
    "~": "in-progress",
    "x": "done",
    "X": "done",
    "!": "blocked",
    "-": "cancelled",
}
MARK_BY_STATE = {
    "todo": " ",
    "in-progress": "~",
    "done": "x",
    "blocked": "!",
    "cancelled": "-",
}
STATE_ALIASES = {
    "todo": "todo", "to-do": "todo", "to_do": "todo", "pending": "todo",
    "open": "todo", "new": "todo",
    "in-progress": "in-progress", "in_progress": "in-progress",
    "inprogress": "in-progress", "doing": "in-progress", "wip": "in-progress",
    "started": "in-progress", "active": "in-progress",
    "done": "done", "complete": "done", "completed": "done",
    "closed": "done",
    "blocked": "blocked", "stuck": "blocked",
    "cancelled": "cancelled", "canceled": "cancelled", "skip": "cancelled",
    "skipped": "cancelled", "dropped": "cancelled",
}
LIVE_STATES = ("todo", "in-progress", "done", "blocked")
STATE_ORDER = ("done", "in-progress", "blocked", "todo", "cancelled")
FILE_STATUS_VALUES = ("planned", "active", "blocked", "done", "archived")
RESERVED_META = ("detail", "owner", "blocked-by", "weight", "due")
STATE_LABELS = {
    "done": "done", "in-progress": "in progress", "blocked": "blocked",
    "todo": "todo", "cancelled": "cancelled",
}

PROG_START = "<!-- tmda:progress:start -->"
PROG_END = "<!-- tmda:progress:end -->"

# --------------------------------------------------------------------------
# regexes
# --------------------------------------------------------------------------

FRONT_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|$)", re.S)
TASK_RE = re.compile(
    r"^(?P<indent> *)(?P<bullet>[-*+])[ \t]+\[(?P<mark>[^\]])\]"
    r"(?P<gap>[ \t]*)(?P<rest>.*)$"
)
TABTASK_RE = re.compile(r"^[ \t]*[-*+][ \t]+\[[^\]]\]")
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
FENCE_RE = re.compile(r"^([ \t]*)(`{3,}|~{3,})")
META_RE = re.compile(
    r"@([A-Za-z][A-Za-z0-9_-]*)="
    r"(\"(?:[^\"\\]|\\.)*\"|'(?:[^']*)'|[^\s@]+)\s*$"
)
ID_RE = re.compile(r"^(T-\d{3,})(?:\s+(.*))?$")
LOOSE_ID_RE = re.compile(r"^[Tt]-\d")
SLUG_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
HTML_ANCHOR_RE = re.compile(r"<a\s+[^>]*(?:name|id)\s*=\s*[\"']([^\"']+)[\"']", re.I)


def prog_regions(text: str):
    """Return [(start, end)] for every well-formed progress region, or None
    when the markers are malformed (stray or unterminated)."""
    regions = []
    pos = 0
    starts = text.count(PROG_START)
    ends = text.count(PROG_END)
    if starts != ends:
        return None
    while True:
        s = text.find(PROG_START, pos)
        if s < 0:
            break
        e = text.find(PROG_END, s + len(PROG_START))
        if e < 0:
            return None
        regions.append((s, e + len(PROG_END)))
        pos = e + len(PROG_END)
    return regions


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def unquote(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw


def slugify(text: str) -> str:
    """Approximate GitHub's heading anchor algorithm."""
    t = re.sub(r"`([^`]*)`", r"\1", text)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"[*_~]{1,3}", "", t)
    t = t.strip().lower()
    t = re.sub(r"[^\w\s-]", "", t, flags=re.UNICODE)
    t = re.sub(r"\s+", "-", t)
    return t


def discover(paths) -> "list[Path]":
    """Files and/or directories -> ordered, de-duplicated .tmda files."""
    out, seen = [], set()
    for raw in (paths or ["."]):
        p = Path(raw)
        if p.is_file():
            found = [p]
        elif p.is_dir():
            found = sorted(
                f for f in p.rglob("*.tmda")
                if not any(part.startswith(".") for part in f.parts)
            )
        else:
            print(f"tmda: no such path: {p}", file=sys.stderr)
            sys.exit(2)
        for f in found:
            key = str(f)
            if key not in seen:
                seen.add(key)
                out.append(f)
    return out


def read_text(path: Path):
    data = path.read_bytes()
    crlf = b"\r\n" in data
    text = data.decode("utf-8", errors="replace")
    bom = text.startswith("\ufeff")
    if bom:
        text = text[1:]
    if crlf:
        text = text.replace("\r\n", "\n")
    return text, crlf, bom


def write_text(path: Path, text: str, crlf: bool = False, bom: bool = False):
    if bom:
        text = "\ufeff" + text
    if crlf:
        text = text.replace("\n", "\r\n")
    path.write_bytes(text.encode("utf-8"))


def progress_of(entries) -> dict:
    """entries: iterable of (state, weight) for non-container tasks."""
    counts = {s: 0 for s in STATE_ORDER}
    live = 0
    done_w = total_w = 0
    any_partial_weight = False
    for state, weight in entries:
        counts[state] += 1
        if state == "cancelled":
            continue
        live += 1
        total_w += weight
        if weight != 1:
            any_partial_weight = True
        if state == "done":
            done_w += weight
    pct = int(round(100.0 * done_w / total_w)) if total_w else 0
    return {
        "percent": pct,
        "weighted": {"done": done_w, "total": total_w},
        "tasks": {
            "done": counts["done"],
            "in_progress": counts["in-progress"],
            "blocked": counts["blocked"],
            "todo": counts["todo"],
            "cancelled": counts["cancelled"],
            "live": live,
        },
        "weighted_points": any_partial_weight,
    }


def derived_status(progress: dict) -> str:
    t = progress["tasks"]
    if progress["weighted"]["total"] == 0:
        return "planned"
    if progress["percent"] == 100:
        return "done"
    if t["done"] or t["in_progress"]:
        return "active"
    if t["blocked"]:
        return "blocked"
    return "planned"


def render_progress_block(progress: dict) -> str:
    if progress["weighted"]["total"] == 0:
        line = "**Progress** - no tasks yet"
    else:
        w = progress["weighted"]
        filled = int(BAR_WIDTH * w["done"] / w["total"] + 0.5)
        bar = "\u2588" * filled + "\u2591" * (BAR_WIDTH - filled)
        suffix = " pts" if progress["weighted_points"] else ""
        line = (
            f"**Progress** `{bar}` "
            f"**{w['done']}/{w['total']}{suffix} ({progress['percent']}%)**"
        )
        extras = []
        t = progress["tasks"]
        for key in ("in_progress", "blocked", "todo", "cancelled"):
            if t[key]:
                extras.append(f"{t[key]} {STATE_LABELS[key.replace('_', '-')]}")
        if extras:
            line += " - " + " - ".join(extras)
    return f"{PROG_START}\n{line}\n{PROG_END}"


def sync_text(text: str, progress: dict):
    """Rewrite (or insert) the managed progress region. Returns (text, note)."""
    block = render_progress_block(progress)
    regions = prog_regions(text)
    if regions is None:
        return text, "malformed progress markers"
    if regions:
        s, e = regions[0]
        new = text[:s] + block + text[e:]
        if len(regions) > 1:
            for rs, re_ in reversed(regions[1:]):
                new = new[:rs] + new[re_:]
        return new, "updated" if new != text else "unchanged"

    lines = text.split("\n")
    had_nl = lines and lines[-1] == ""
    if had_nl:
        lines.pop()          # drop the phantom element after the final newline

    idx = 0
    if lines and lines[0] == "---":
        try:
            idx = lines.index("---", 1) + 1
        except ValueError:
            idx = 0
    seen_h1 = False
    while idx < len(lines):
        raw = lines[idx]
        stripped = raw.strip()
        if not stripped:
            idx += 1
            continue
        if stripped.startswith("# ") and not seen_h1:
            seen_h1 = True
            idx += 1
            continue
        if stripped.startswith(">"):
            idx += 1
            continue
        break
    pre = "" if idx == 0 or not lines[idx - 1].strip() else "\n"
    suffix = "\n" if idx >= len(lines) or lines[idx].strip() else ""
    lines.insert(idx, f"{pre}{block}{suffix}")
    out = "\n".join(lines)
    if had_nl:
        out += "\n"
    return out, "inserted"


# --------------------------------------------------------------------------
# front matter (YAML subset unless PyYAML is installed)
# --------------------------------------------------------------------------


def _scalar(raw: str):
    v = raw.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    if v in ("", "null", "~", "Null", "NULL"):
        return None
    if v in ("true", "True", "TRUE"):
        return True
    if v in ("false", "False", "FALSE"):
        return False
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def _split_inline_list(body: str) -> list:
    items, buf, quote = [], "", None
    for ch in body:
        if quote:
            buf += ch
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            buf += ch
        elif ch == ",":
            items.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        items.append(buf.strip())
    return [_scalar(i) for i in items]


def mini_yaml(text: str):
    """Parse the documented front-matter subset: scalars, inline lists and
    block lists. Raises ValueError on anything outside that subset."""
    data = {}
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped in ("---", "..."):
            continue
        if line[0] in " \t":
            raise ValueError(f"unexpected indented line {i}: {stripped!r}")
        m = re.match(r"^([A-Za-z_][\w.-]*)\s*:(.*)$", stripped)
        if not m:
            raise ValueError(f"expected 'key: value' at line {i}: {stripped!r}")
        key, rest = m.group(1), m.group(2).strip()
        if rest:
            if rest.startswith("["):
                if not rest.endswith("]"):
                    raise ValueError(f"unterminated inline list for {key!r}")
                data[key] = _split_inline_list(rest[1:-1])
            elif rest.startswith("{"):
                raise ValueError(f"mapping values are not supported: {key!r}")
            else:
                data[key] = _scalar(rest)
            continue
        items = []
        while i < len(lines):
            nxt = lines[i]
            ns = nxt.strip()
            if not ns or ns.startswith("#"):
                i += 1
                continue
            if nxt[:1] not in (" ", "\t"):
                break
            if ns.startswith("- "):
                items.append(_scalar(ns[2:].strip()))
                i += 1
                continue
            raise ValueError(f"expected a block list item at line {i + 1}")
        data[key] = items
    return data


def parse_front_matter(text: str):
    """-> (data, body, issues) where issues is a list of (severity, code, msg)."""
    issues = []
    m = FRONT_RE.match(text)
    if not m:
        return None, text, issues
    raw = m.group(1)
    body = text[m.end():]
    try:
        import yaml  # type: ignore
    except ImportError:
        yaml = None
    if yaml is not None:
        try:
            data = yaml.safe_load(raw)
        except Exception as exc:  # noqa: BLE001 - surface whatever YAML says
            return None, body, [("error", "E002", f"front matter is not valid YAML: {exc}")]
        if data is None:
            data = {}
        if not isinstance(data, dict):
            return None, body, [("error", "E002", "front matter must be a mapping")]
        return data, body, issues
    try:
        data = mini_yaml(raw)
    except ValueError as exc:
        return None, body, [(
            "error", "E002",
            f"front matter parse error: {exc} (the built-in parser accepts a "
            "YAML subset - see SPEC.md 6; `pip install 'tmda[yaml]'` adds "
            "full YAML support)",
        )]
    return data, body, issues


# --------------------------------------------------------------------------
# front-matter field checks
# --------------------------------------------------------------------------


def validate_front_matter(data, path: Path) -> list:
    out = []

    def err(code, msg):
        out.append(("error", code, msg))

    def warn(code, msg):
        out.append(("warning", code, msg))

    ver = data.get("tmda")
    if ver is None:
        err("E003", "front matter is missing required key 'tmda'")
    elif not isinstance(ver, int) or isinstance(ver, bool):
        err("E003", f"'tmda' must be an integer, got {ver!r}")
    elif ver != SPEC_VERSION:
        err("E003", f"unsupported spec version {ver} (this tool implements {SPEC_VERSION})")

    fid = data.get("id")
    if fid is None:
        err("E004", "front matter is missing required key 'id'")
    elif not isinstance(fid, str) or not SLUG_ID_RE.match(fid):
        err("E005", f"'id' must be a lowercase slug, got {fid!r}")

    if data.get("title") in (None, ""):
        err("E006", "front matter is missing required key 'title'")

    detail = data.get("detail")
    if detail in (None, ""):
        err("E007", "front matter is missing required key 'detail' "
                    "(the .tmda file must link to a detailed brief)")
    elif isinstance(detail, str):
        _, link_issues = resolve_link(detail, path)
        for sev, code, msg in link_issues:
            out.append((sev, code, f"detail: {msg}"))
    else:
        err("E007", f"'detail' must be a string path, got {detail!r}")

    status = data.get("status")
    if status is not None and status not in FILE_STATUS_VALUES:
        err("E010", f"'status' must be one of {', '.join(FILE_STATUS_VALUES)}, got {status!r}")

    updated = data.get("updated")
    if updated is not None:
        if not isinstance(updated, (date, str)) or (
            isinstance(updated, str) and not _is_iso_date(updated)
        ):
            err("E011", f"'updated' must be an ISO date (YYYY-MM-DD), got {updated!r}")

    owner = data.get("owner")
    if owner is not None and not isinstance(owner, str):
        err("E012", "'owner' must be a string")

    deps = data.get("depends_on")
    if deps is not None and not isinstance(deps, (list, tuple, str)):
        err("E013", "'depends_on' must be a string or a list of strings")

    unknown = sorted(set(data) - {
        "tmda", "id", "title", "detail", "status", "owner", "updated",
        "depends_on", "tags", "agent",
    })
    if unknown:
        warn("W068", "unknown front-matter key(s): " + ", ".join(unknown))
    return out


def _is_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def resolve_link(value: str, base: Path):
    """Resolve a detail link relative to the .tmda file.

    Returns (target_path_or_None, issues). Issues are (severity, code, msg).
    """
    issues = []
    ref, _, frag = value.partition("#")
    ref = ref.strip()
    if not ref:
        if frag:
            issues.append(("error", "E009", f"same-document anchor #{frag} is not supported; "
                                            "use a path to a brief"))
        return None, issues
    target = (base.parent / ref).resolve() if not os.path.isabs(ref) else Path(ref)
    if not target.exists():
        issues.append(("error", "E008", f"link target does not exist: {ref}"))
        return None, issues
    if frag and target.is_file():
        anchors = collect_anchors(target)
        if slugify(frag) not in anchors and frag not in anchors:
            issues.append(("error", "E009",
                           f"anchor '#{frag}' not found in {ref}"))
    return target, issues


def collect_anchors(path: Path) -> set:
    try:
        text, _, _ = read_text(path)
    except OSError:
        return set()
    anchors = set()
    in_fence = False
    fence = None
    for line in text.split("\n"):
        fm = FENCE_RE.match(line)
        if fm:
            marker = fm.group(2)
            if not in_fence:
                in_fence, fence = True, marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                in_fence = False
            continue
        if in_fence:
            continue
        hm = HEADING_RE.match(line)
        if hm:
            anchors.add(slugify(hm.group(2)))
        for am in HTML_ANCHOR_RE.finditer(line):
            anchors.add(am.group(1))
    return anchors


# --------------------------------------------------------------------------
# document model
# --------------------------------------------------------------------------


@dataclass
class Task:
    id: "str | None"
    text: str
    state: str
    mark: str
    meta: dict
    line: int          # 1-based line number in the source file
    indent: int
    bullet: str
    section: "int | None" = None
    parent: "int | None" = None
    children: list = field(default_factory=list)
    container: bool = False
    derived: str = "todo"
    weight: int = 1

    def to_json(self, sections) -> dict:
        return {
            "id": self.id,
            "state": self.derived if self.container else self.state,
            "authored_state": self.state,
            "container": self.container,
            "text": self.text,
            "line": self.line,
            "indent": self.indent,
            "parent": None,          # filled in by report_to_json
            "section": sections[self.section].title if self.section is not None else None,
            "detail": self.meta.get("detail"),
            "owner": self.meta.get("owner"),
            "blocked_by": [
                b for b in (self.meta.get("blocked-by") or "").split(",") if b
            ],
            "due": self.meta.get("due"),
            "weight": self.weight,
        }


@dataclass
class Section:
    title: str
    level: int
    line: int
    parent: "int | None" = None
    children: list = field(default_factory=list)
    tasks: list = field(default_factory=list)
    indices: list = field(default_factory=list)


@dataclass
class Issue:
    severity: str
    code: str
    line: "int | None"
    message: str

    def render(self) -> str:
        where = f"line {self.line}: " if self.line else ""
        return f"{self.severity}  {self.code}  {where}{self.message}"


@dataclass
class FileReport:
    path: Path
    text: str
    crlf: bool = False
    bom: bool = False
    front: dict = field(default_factory=dict)
    has_front: bool = False
    tasks: list = field(default_factory=list)
    sections: list = field(default_factory=list)
    h1: "str | None" = None
    issues: list = field(default_factory=list)
    progress: dict = field(default_factory=dict)
    status: str = "planned"
    authored_status: "str | None" = None
    has_progress: bool = False
    stale_progress: bool = False
    unsectioned: list = field(default_factory=list)
    crlf_note: str = ""

    @property
    def id(self):
        return self.front.get("id")

    @property
    def title(self):
        return self.front.get("title") or self.h1 or self.path.name

    @property
    def archived(self):
        return self.front.get("status") == "archived"

    def error_count(self):
        return sum(1 for i in self.issues if i.severity == "error")

    def warn_count(self):
        return sum(1 for i in self.issues if i.severity == "warning")

    def add(self, severity, code, message, line=None):
        self.issues.append(Issue(severity, code, line, message))

    # -- convenience -------------------------------------------------------
    def flat_tasks(self):
        return [t for t in self.tasks if not t.container]

    def blocked(self):
        return [t for t in self.flat_tasks() if t.derived == "blocked"]

    def next_up(self, limit=5):
        done = {t.id for t in self.tasks if t.state == "done"}
        out = []
        for t in self.flat_tasks():
            if t.state not in ("todo", "in-progress"):
                continue
            deps = [b for b in (t.meta.get("blocked-by") or "").split(",") if b]
            if any(d not in done for d in deps):
                continue
            out.append(t)
            if limit and len(out) >= limit:
                break
        return out


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------


def parse_document(path: Path) -> FileReport:
    text, crlf, bom = read_text(path)
    rep = FileReport(path=path, text=text, crlf=crlf, bom=bom)

    data, body, fm_issues = parse_front_matter(text)
    rep.has_front = data is not None
    rep.front = data or {}
    for sev, code, msg in fm_issues:
        rep.add(sev, code, msg, 1)
    if data is None and not fm_issues:
        rep.add("error", "E001", "missing front matter (--- ... --- at byte 0)", 1)
    if data is not None:
        for sev, code, msg in validate_front_matter(data, path):
            rep.add(sev, code, msg, 1)

    offset = text[:len(text) - len(body)].count("\n")
    lines = body.split("\n")
    # a trailing newline produces a final empty element; keep line numbers sane
    if lines and lines[-1] == "":
        lines.pop()

    in_fence = False
    fence_marker = None
    sec_stack: list = []
    task_stack: list = []   # indices of open tasks, descending indent

    for n, raw in enumerate(lines, start=1):
        lineno = n + offset

        fm = FENCE_RE.match(raw)
        if fm and not in_fence:
            in_fence, fence_marker = True, fm.group(2)
            continue
        if in_fence:
            if fm and fm.group(2)[0] == fence_marker[0] and len(fm.group(2)) >= len(fence_marker):
                in_fence = False
            continue

        if PROG_START in raw or PROG_END in raw:
            continue

        hm = HEADING_RE.match(raw)
        if hm:
            level = len(hm.group(1))
            title = hm.group(2).strip()
            if level == 1:
                rep.h1 = title
            else:
                while sec_stack and rep.sections[sec_stack[-1]].level >= level:
                    sec_stack.pop()
                node = Section(title=title, level=level, line=lineno,
                               parent=sec_stack[-1] if sec_stack else None)
                idx = len(rep.sections)
                rep.sections.append(node)
                if node.parent is not None:
                    rep.sections[node.parent].children.append(idx)
                sec_stack.append(idx)
            continue

        if TABTASK_RE.match(raw) and ("\t" in raw[:raw.index("[")]):
            rep.add("error", "E020",
                    "task line uses tab indentation; use spaces", lineno)
            continue

        tm = TASK_RE.match(raw)
        if not tm:
            continue

        mark = tm.group("mark")
        indent = len(tm.group("indent"))
        rest = tm.group("rest")

        if "\t" in tm.group(0):
            rep.add("error", "E020",
                    "task line contains a tab; use spaces", lineno)
            continue

        if mark not in STATE_BY_MARK:
            rep.add("error", "E021",
                    f"unknown task state marker [{mark}]; valid markers are "
                    "[ ] [~] [x] [!] [-]", lineno)
            continue

        if not rest.strip():
            rep.add("error", "E022", "task has no id and no text", lineno)
            continue

        meta, remaining, dupes = parse_meta(rest)
        for d in dupes:
            rep.add("error", "E034",
                    f"duplicate metadata key @{d} on one task", lineno)

        task_id = None
        idm = ID_RE.match(remaining)
        if idm:
            task_id = idm.group(1)
            remaining = (idm.group(2) or "").strip()
        elif LOOSE_ID_RE.match(remaining):
            rep.add("error", "E024",
                    f"task id {remaining.split()[0]!r} must be uppercase 'T-' "
                    "followed by at least 3 digits", lineno)
            remaining = remaining.strip()
        else:
            rep.add("error", "E022",
                    "task is missing an id (expected 'T-001' immediately after "
                    "the checkbox)", lineno)
            remaining = remaining.strip()

        if not remaining:
            rep.add("error", "E022", "task has no description text", lineno)

        weight_raw = meta.get("weight")
        weight = 1
        if weight_raw is not None:
            if not re.fullmatch(r"[1-9]\d*", str(weight_raw)):
                rep.add("error", "E031",
                        f"@weight must be a positive integer, got {weight_raw!r}",
                        lineno)
            else:
                weight = int(weight_raw)

        due = meta.get("due")
        if due is not None and not _is_iso_date(str(due)):
            rep.add("error", "E032", f"@due must be an ISO date, got {due!r}", lineno)

        unknown = sorted(set(meta) - set(RESERVED_META))
        if unknown:
            rep.add("warning", "W069",
                    "unknown task metadata key(s): "
                    + ", ".join("@" + u for u in unknown), lineno)

        if "detail" in meta:
            _, link_issues = resolve_link(meta["detail"], path)
            for sev, code, msg in link_issues:
                rep.add(sev, code, f"task detail: {msg}", lineno)

        tid = len(rep.tasks)
        task = Task(
            id=task_id, text=remaining, state=STATE_BY_MARK[mark], mark=mark,
            meta=meta, line=lineno, indent=indent, bullet=tm.group("bullet"),
            section=sec_stack[-1] if sec_stack else None,
            weight=weight,
        )
        rep.tasks.append(task)

        while task_stack and rep.tasks[task_stack[-1]].indent >= indent:
            task_stack.pop()
        if task_stack:
            parent = rep.tasks[task_stack[-1]]
            parent.children.append(tid)
            task.parent = task_stack[-1]
        elif indent > 0:
            rep.add("warning", "W063",
                    "indented task has no parent task at a lower indent", lineno)
        task_stack.append(tid)

        if task.section is not None:
            rep.sections[task.section].tasks.append(tid)
        else:
            rep.unsectioned.append(tid)

    return rep


def parse_meta(rest: str):
    """Split trailing @key=value tokens off the end of a task line.

    Scans right-to-left so unquoted values may not contain spaces (quote them
    instead) while quoted values may.
    """
    meta, dupes = {}, []
    line = rest
    while True:
        m = META_RE.search(line)
        if not m:
            break
        key = m.group(1)
        value = unquote(m.group(2))
        if key in meta:
            dupes.append(key)
        else:
            meta[key] = value
        line = line[:m.start()].rstrip()
    return meta, line, dupes


# --------------------------------------------------------------------------
# analysis: containers, derived states, rollups
# --------------------------------------------------------------------------


def analyze(path: Path) -> FileReport:
    rep = parse_document(path)

    tasks = rep.tasks
    # children always have a higher index than their parent, so a single
    # reverse pass derives every container bottom-up.
    for i in reversed(range(len(tasks))):
        if tasks[i].children:
            tasks[i].container = True
            live = [tasks[j].derived for j in tasks[i].children
                    if tasks[j].derived != "cancelled"]
            if not live:
                tasks[i].derived = "cancelled"
            elif "blocked" in live:
                tasks[i].derived = "blocked"
            elif "in-progress" in live:
                tasks[i].derived = "in-progress"
            elif all(s == "done" for s in live):
                tasks[i].derived = "done"
            else:
                tasks[i].derived = "todo"
        else:
            tasks[i].derived = tasks[i].state

    # ---- task-level validation -----------------------------------------
    seen_ids = {}
    for t in tasks:
        if t.id is None:
            continue
        if t.id in seen_ids:
            rep.add("error", "E023",
                    f"duplicate task id {t.id} (first used on line "
                    f"{seen_ids[t.id]})", t.line)
        else:
            seen_ids[t.id] = t.line

    id_set = set(seen_ids)
    for t in tasks:
        raw = t.meta.get("blocked-by")
        if not raw:
            continue
        for ref in [r.strip() for r in str(raw).split(",") if r.strip()]:
            if ref not in id_set:
                rep.add("error", "E033",
                        f"@blocked-by references unknown task {ref}", t.line)

    # ---- section rollups ------------------------------------------------
    def collect(i: int) -> list:
        if rep.sections[i].indices:
            return rep.sections[i].indices
        idx = list(rep.sections[i].tasks)
        for c in rep.sections[i].children:
            idx.extend(collect(c))
        rep.sections[i].indices = idx
        return idx

    for i in range(len(rep.sections)):
        collect(i)

    rep.unsectioned = [i for i in rep.unsectioned]

    def entries_for(indices):
        return [(tasks[i].derived, tasks[i].weight)
                for i in indices if not tasks[i].container]

    rep.progress = progress_of(entries_for(
        [i for i in range(len(tasks)) if not tasks[i].container]))

    rep.section_progress = {}
    for i, sec in enumerate(rep.sections):
        rep.section_progress[i] = progress_of(entries_for(sec.indices))
    rep.top_progress = progress_of(entries_for(rep.unsectioned))

    rep.status = derived_status(rep.progress)
    rep.authored_status = rep.front.get("status")

    # ---- authored vs derived status ------------------------------------
    authored = rep.authored_status
    if authored == "done" and rep.status != "done":
        remaining = rep.progress["tasks"]["live"] - rep.progress["tasks"]["done"]
        rep.add("warning", "W061",
                f"front matter says status 'done' but {remaining} live "
                "task(s) are not done")
    elif authored in ("planned", "active", "blocked") and rep.status == "done":
        rep.add("warning", "W061",
                f"front matter says status {authored!r} but every task is done "
                "(set status: done or archived)")

    # ---- containers marked done by hand --------------------------------
    for t in tasks:
        if not t.container or t.state != "done":
            continue
        incomplete = [c for c in t.children
                      if tasks[c].derived not in ("done", "cancelled")]
        if incomplete:
            rep.add("warning", "W062",
                    f"container {t.id or '?'} is marked [x] but "
                    f"{len(incomplete)} of its children are not done "
                    "(container state is derived from its children)", t.line)

    # ---- progress block -------------------------------------------------
    regions = prog_regions(rep.text)
    if regions is None:
        rep.add("error", "E041",
                "malformed tmda progress markers (every start needs a matching "
                "end)")
    else:
        rep.has_progress = bool(regions)
        if len(regions) > 1:
            rep.add("error", "E041",
                    "found more than one tmda progress block")
        elif regions:
            s, e = regions[0]
            current = rep.text[s:e]
            expected = render_progress_block(rep.progress)
            if _region_body(current) != _region_body(expected):
                rep.stale_progress = True
                rep.add("error", "E040",
                        "progress block is stale; run `tmda sync`")
        else:
            rep.add("warning", "W060",
                    "no progress block; run `tmda sync` to add one")

    # ---- brief sanity ---------------------------------------------------
    if rep.has_front:
        detail = rep.front.get("detail")
        if isinstance(detail, str) and detail:
            target = (rep.path.parent / detail.partition("#")[0]).resolve()
            if target.is_file():
                try:
                    brief, _, _ = read_text(target)
                except OSError:
                    brief = ""
                if rep.path.name not in brief and str(rep.path) not in brief:
                    rep.add("warning", "W066",
                            f"brief {detail} does not link back to "
                            f"{rep.path.name}")

        deps = rep.front.get("depends_on")
        if deps is not None and not isinstance(deps, (list, tuple, str)):
            pass  # already reported as E013

        if not tasks:
            rep.add("warning", "W064", "file contains no tasks")

        title = rep.front.get("title")
        if isinstance(title, str) and rep.h1 and title != rep.h1:
            rep.add("warning", "W065",
                    f"H1 {rep.h1!r} differs from front-matter title {title!r}")

    return rep


def _region_body(block: str) -> str:
    inner = block.strip()
    inner = inner[len(PROG_START):]
    if inner.endswith(PROG_END):
        inner = inner[:-len(PROG_END)]
    return inner.strip()


def project_check(reports) -> list:
    """Cross-file validation. Returns [Issue-like tuples]."""
    out = []
    by_id = {}
    for r in reports:
        fid = r.id
        if not fid:
            continue
        if fid in by_id:
            out.append(Issue("error", "E050", None,
                             f"file id {fid!r} used by both "
                             f"{by_id[fid].path} and {r.path}"))
        else:
            by_id[fid] = r
    known = set(by_id)
    for r in reports:
        deps = r.front.get("depends_on")
        if isinstance(deps, str):
            deps = [deps]
        for dep in deps or []:
            if not isinstance(dep, str):
                continue
            if (r.path.parent / dep).exists() or (
                    r.path.parent / f"{dep}.tmda").exists():
                continue
            if dep in known:
                continue
            out.append(Issue("error", "E051", None,
                             f"{r.path}: depends_on target not found: {dep}"))
    return out


# --------------------------------------------------------------------------
# output helpers
# --------------------------------------------------------------------------


def _color() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def paint(text: str, code: str) -> str:
    if not _color():
        return text
    return f"\033[{code}m{text}\033[0m"


def red(t):
    return paint(t, "31")


def yellow(t):
    return paint(t, "33")


def green(t):
    return paint(t, "32")


def dim(t):
    return paint(t, "2")


def bold(t):
    return paint(t, "1")


def status_symbol(p: dict) -> str:
    if p["weighted"]["total"] == 0:
        return "-"
    if p["percent"] == 100:
        return green("\u2713")
    if p["tasks"]["blocked"]:
        return red("\u2716")
    if p["percent"] > 0 or p["tasks"]["in_progress"]:
        return paint("\u25b8", "36")
    return "\u25cb"


def fmt_progress(p: dict) -> str:
    if p["weighted"]["total"] == 0:
        return "no tasks"
    w = p["weighted"]
    suffix = " pts" if p["weighted_points"] else ""
    return f"{w['done']}/{w['total']}{suffix} ({p['percent']}%)"


def fmt_counts(p: dict) -> str:
    t = p["tasks"]
    parts = []
    for key in ("done", "in_progress", "blocked", "todo", "cancelled"):
        if t[key]:
            parts.append(f"{t[key]} {STATE_LABELS[key.replace('_', '-')]}")
    return " - ".join(parts) if parts else "no tasks"


def fmt_bar(p: dict, width: int = 20) -> str:
    if p["weighted"]["total"] == 0:
        return ""
    w = p["weighted"]
    filled = int(round(width * w["done"] / w["total"]))
    return "\u2588" * filled + "\u2591" * (width - filled)


def report_to_json(rep: FileReport) -> dict:
    sections = []
    for i, sec in enumerate(rep.sections):
        sections.append({
            "title": sec.title,
            "level": sec.level,
            "line": sec.line,
            "parent_line": rep.sections[sec.parent].line if sec.parent is not None else None,
            "progress": rep.section_progress[i],
        })
    tasks = []
    for t in rep.tasks:
        item = t.to_json(rep.sections)
        item["parent"] = (rep.tasks[t.parent].id
                          if t.parent is not None else None)
        tasks.append(item)
    return {
        "path": str(rep.path),
        "id": rep.id,
        "title": rep.title,
        "detail": rep.front.get("detail"),
        "owner": rep.front.get("owner"),
        "authored_status": rep.authored_status,
        "status": rep.status,
        "archived": rep.archived,
        "progress": rep.progress,
        "top_progress": rep.top_progress if rep.unsectioned else None,
        "sections": sections,
        "tasks": tasks,
        "next": [t.id for t in rep.next_up()],
        "blocked": [t.id for t in rep.blocked()],
        "errors": [i.render() for i in rep.issues if i.severity == "error"],
        "warnings": [i.render() for i in rep.issues if i.severity == "warning"],
    }


def print_issues(rep: FileReport) -> None:
    if not rep.issues:
        return
    print(f"{rep.path}")
    for issue in sorted(rep.issues, key=lambda i: (i.line or 0, i.code)):
        text = issue.render()
        if issue.severity == "error":
            print(f"  {red(text)}")
        else:
            print(f"  {yellow(text)}")
    print()


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


BRIEF_TEMPLATE = """# {title} - Brief

Task file: [{name}](./{name}) - open it to see tracked progress.

## What we want to do

Describe the outcome in plain language. This is the "why" behind every
checkbox in `{name}`; an agent should read this section before it touches
anything in the task file.

{goal}

## Why it matters

{why}

## Constraints and non-goals

- 

## What "done" looks like

- 
"""


TMDA_TEMPLATE = """---
tmda: 1
id: {id}
title: {title}
detail: {detail}
status: planned
owner: {owner}
updated: {updated}
---

# {title}

> **Brief:** [{detail}]({detail}) - what we are trying to do and why.
> Read it before changing any checkbox below.

<!-- tmda:progress:start -->
<!-- tmda:progress:end -->

## {section}

- [ ] T-001 First task @owner={owner} @weight=1
"""


def cmd_init(args) -> int:
    name = args.name
    if not name.endswith(".tmda"):
        name += ".tmda"
    path = Path(name)
    if path.exists() and not args.force:
        print(f"tmda: {path} already exists (use --force to overwrite)",
              file=sys.stderr)
        return 2

    stem = path.stem
    slug = slugify(stem)
    if not SLUG_ID_RE.match(slug or ""):
        slug = "task"
    fid = args.id or slug
    title = args.title or stem.replace("-", " ").replace("_", " ")
    owner = args.owner or os.environ.get("USER") or "unknown"
    today = date.today().isoformat()

    detail = args.detail or f"./{stem}-brief.md"
    brief_path = (path.parent / detail.partition("#")[0]).resolve()

    path.parent.mkdir(parents=True, exist_ok=True)

    if not args.no_brief:
        if not brief_path.exists():
            brief_path.parent.mkdir(parents=True, exist_ok=True)
            brief_path.write_text(
                BRIEF_TEMPLATE.format(
                    title=title, name=path.name,
                    goal="TODO", why="TODO",
                ),
                encoding="utf-8",
            )
            print(f"created  {brief_path}")
        else:
            print(f"kept     {brief_path}")

    body = TMDA_TEMPLATE.format(
        id=fid, title=title, detail=detail, owner=owner, updated=today,
        section=args.section,
    )
    write_text(path, body)
    print(f"created  {path}")

    rep = analyze(path)
    new_text, note = sync_text(rep.text, rep.progress)
    if note != "unchanged":
        write_text(path, new_text, rep.crlf, rep.bom)
        print(f"progress {note}")
        rep = analyze(path)

    errs = [i for i in rep.issues if i.severity == "error"]
    for issue in errs:
        print(f"  {red(issue.render())}")
    if errs:
        if any(i.code in ("E008", "E009") for i in errs):
            print(f"tmda: detail link {detail!r} does not resolve; pass "
                  "--detail <path>, or drop --no-brief so it gets created")
        else:
            print("tmda: scaffold has errors; fix them and run `tmda check`")
        return 1
    print(f"next:    edit {path} and run `tmda check`")
    return 0


def _collect(args):
    files = discover(args.paths)
    if not files:
        print("tmda: no .tmda files found", file=sys.stderr)
        sys.exit(2)
    return [analyze(p) for p in files]


def cmd_check(args) -> int:
    reports = _collect(args)
    cross = project_check(reports)
    strict = args.strict

    if args.json:
        payload = {
            "spec": SPEC_VERSION,
            "files": [report_to_json(r) for r in reports],
            "cross_file_errors": [i.render() for i in cross],
            "errors": sum(r.error_count() for r in reports)
                      + sum(1 for i in cross if i.severity == "error"),
            "warnings": sum(r.warn_count() for r in reports)
                        + sum(1 for i in cross if i.severity == "warning"),
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"tmda check: {len(reports)} file(s)")
        print()
        for rep in reports:
            print_issues(rep)
        for issue in cross:
            if issue.severity == "error":
                print(red(issue.render()))
            else:
                print(yellow(issue.render()))
        errors = sum(r.error_count() for r in reports) + \
            sum(1 for i in cross if i.severity == "error")
        warnings = sum(r.warn_count() for r in reports) + \
            sum(1 for i in cross if i.severity == "warning")
        bad = errors + (warnings if strict else 0)
        mark = red("FAIL") if bad else green("OK")
        tail = f" - {errors} error(s), {warnings} warning(s)"
        if strict:
            tail += " (strict: warnings are fatal)"
        print(f"{mark}{tail}")

    errors = sum(r.error_count() for r in reports) + \
        sum(1 for i in cross if i.severity == "error")
    warnings = sum(r.warn_count() for r in reports) + \
        sum(1 for i in cross if i.severity == "warning")
    if errors:
        return 1
    if strict and warnings:
        return 1
    return 0


def _print_file_status(rep: FileReport, show_tasks=True) -> None:
    head = f"{rep.path}"
    print(bold(head))
    print(f"  {rep.title}")
    detail = rep.front.get("detail") or "(no brief linked)"
    link_ok = "ok" if not any(
        i.code in ("E008", "E009") for i in rep.issues) else red("BROKEN")
    print(f"  brief    {detail}  {link_ok}")
    status_txt = rep.status
    if rep.authored_status and rep.authored_status != rep.status:
        status_txt += f" (front matter says {rep.authored_status})"
    print(f"  status   {status_symbol(rep.progress)} {status_txt}")

    bar = fmt_bar(rep.progress)
    if bar:
        print(f"  progress `{bar}`  **{fmt_progress(rep.progress)}**")
    print(f"           {fmt_counts(rep.progress)}")

    if rep.sections or rep.unsectioned:
        print()
        print("  sections")
        if rep.unsectioned:
            p = rep.top_progress
            print(f"    {status_symbol(p)} {fmt_progress(p):>10}  "
                  f"{dim('(no heading)')}")
        for i, sec in enumerate(rep.sections):
            p = rep.section_progress[i]
            pad = "  " * (sec.level - 2)
            label = f"{pad}{sec.title}"
            print(f"    {status_symbol(p)} {fmt_progress(p):>10}  {label}")

    if show_tasks:
        blocked = rep.blocked()
        if blocked:
            print()
            print("  " + red("blocked"))
            for t in blocked:
                deps = ", ".join(d for d in
                                 (t.meta.get("blocked-by") or "").split(",") if d)
                suffix = f"  {dim('blocked-by ' + deps)}" if deps else ""
                print(f"    {red(t.id)}  {t.text}{suffix}")
        nxt = rep.next_up()
        if nxt:
            print()
            print("  next up")
            for t in nxt:
                owner = t.meta.get("owner")
                tail = f"  {dim('@' + owner)}" if owner else ""
                print(f"    {t.id}  {t.text}{tail}")
    print()


def cmd_status(args) -> int:
    reports = _collect(args)
    if args.json:
        live = [r for r in reports if not r.archived]
        totals = progress_of(
            (t.derived, t.weight) for r in live for t in r.flat_tasks())
        payload = {
            "spec": SPEC_VERSION,
            "files": [report_to_json(r) for r in reports],
            "totals": totals,
            "totals_status": derived_status(totals),
            "errors": sum(r.error_count() for r in reports),
            "warnings": sum(r.warn_count() for r in reports),
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    for rep in reports:
        _print_file_status(rep, show_tasks=not args.no_tasks)
    live = [r for r in reports if not r.archived]
    if len(live) > 1:
        totals = progress_of(
            (t.derived, t.weight) for r in live for t in r.flat_tasks())
        print(bold("TOTAL") + f"  {len(live)} file(s)")
        bar = fmt_bar(totals)
        if bar:
            print(f"  progress `{bar}`  **{fmt_progress(totals)}**")
        print(f"           {fmt_counts(totals)}")
    return 0


def cmd_sync(args) -> int:
    reports = _collect(args)
    changed = 0
    for rep in reports:
        new_text, note = sync_text(rep.text, rep.progress)
        if new_text != rep.text:
            write_text(rep.path, new_text, rep.crlf, rep.bom)
            changed += 1
            print(f"{rep.path}: {note}")
        elif args.verbose:
            print(f"{rep.path}: unchanged")
    if changed:
        print(f"tmda sync: updated {changed} file(s)")
    elif not args.verbose:
        print("tmda sync: everything already up to date")
    return 0


def cmd_list(args) -> int:
    reports = _collect(args)
    wanted = set()
    for s in args.state or []:
        s = s.strip().lower()
        if s not in STATE_ALIASES:
            print(f"tmda: unknown state {s!r} "
                  f"(choose from {', '.join(sorted(set(STATE_ALIASES.values())))})",
                  file=sys.stderr)
            return 2
        wanted.add(STATE_ALIASES[s])

    rows = []
    for rep in reports:
        for t in rep.flat_tasks():
            if wanted and t.derived not in wanted:
                continue
            if args.open and t.derived not in ("todo", "in-progress", "blocked"):
                continue
            if args.owner:
                if (t.meta.get("owner") or "") != args.owner:
                    continue
            if args.blocked_only and t.derived != "blocked":
                continue
            if args.id and t.id != args.id:
                continue
            rows.append((rep, t))

    if args.json:
        payload = {
            "spec": SPEC_VERSION,
            "count": len(rows),
            "tasks": [dict(t.to_json(rep.sections),
                           path=str(rep.path),
                           file_id=rep.id)
                      for rep, t in rows],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if not rows:
        print("no tasks match")
        return 0
    wpath = max(len(str(r.path)) for r, _ in rows)
    wid = max(len(t.id or "-") for _, t in rows)
    for rep, t in rows:
        state = t.derived
        label = f"[{state}]"
        if state == "done":
            label = green(label)
        elif state == "blocked":
            label = red(label)
        elif state == "in-progress":
            label = paint(label, "36")
        owner = t.meta.get("owner") or ""
        owner = f"  @{owner}" if owner else ""
        print(f"{str(rep.path):<{wpath}}  {t.id or '-':<{wid}}  "
              f"{label:<14}  {t.text}{owner}")
    return 0


def cmd_mark(args) -> int:
    state = args.state.strip().lower()
    if state not in STATE_ALIASES:
        print(f"tmda: unknown state {args.state!r} "
              f"(choose from {', '.join(sorted(set(STATE_ALIASES.values())))})",
              file=sys.stderr)
        return 2
    state = STATE_ALIASES[state]
    new_mark = MARK_BY_STATE[state]

    if args.file:
        paths = [args.file]
    elif getattr(args, "paths", None):
        paths = args.paths
    else:
        paths = None
    files = discover(paths)
    matches = []
    for p in files:
        rep = analyze(p)
        for t in rep.tasks:
            if t.id == args.id:
                matches.append((p, rep, t))

    if not matches:
        print(f"tmda: no task {args.id} found", file=sys.stderr)
        return 2
    if len(matches) > 1:
        where = ", ".join(str(p) for p, _, _ in matches)
        print(f"tmda: {args.id} matches several files ({where}); "
              "narrow it with --file", file=sys.stderr)
        return 2

    p, rep, task = matches[0]
    text, crlf, bom = read_text(p)
    lines = text.split("\n")
    idx = task.line - 1
    if idx < 0 or idx >= len(lines):
        print(f"tmda: cannot locate line {task.line} in {p}", file=sys.stderr)
        return 2

    line = lines[idx]
    tm = TASK_RE.match(line)
    if not tm:
        print(f"tmda: line {task.line} of {p} is no longer a task line",
              file=sys.stderr)
        return 2
    s, e = tm.start("mark"), tm.end("mark")
    if line[s] == new_mark:
        print(f"{p}: {task.id} is already [{new_mark}] ({state})")
    else:
        lines[idx] = line[:s] + new_mark + line[e:]
        write_text(p, "\n".join(lines), crlf, bom)
        print(f"{p}: {task.id} -> [{new_mark}] {state}")

    if task.container:
        print(yellow(f"  note: {task.id} has children; its displayed state is "
                     "derived from them"))

    if not args.no_sync:
        rep = analyze(p)
        new_text, note = sync_text(rep.text, rep.progress)
        if new_text != rep.text:
            write_text(p, new_text, rep.crlf, rep.bom)
            print(f"  progress block {note}")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tmda",
        description="Task Markdown for Agents (.tmda) - author, validate and "
                    "report on agent-readable task files.",
        epilog="Exit codes: 0 = ok, 1 = validation errors, 2 = usage error.",
    )
    p.add_argument("--version", action="version",
                   version=f"tmda {__version__} (spec {SPEC_VERSION})")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    def add_paths(sp):
        sp.add_argument("paths", nargs="*", metavar="PATH",
                        help=".tmda files or directories (default: .)")

    sp = sub.add_parser("init", help="scaffold a new .tmda file and brief")
    sp.add_argument("name", help="file name, e.g. 'auth-flow' or 'auth.tmda'")
    sp.add_argument("--title", help="human title (default: derived from name)")
    sp.add_argument("--id", help="stable file id (default: slug of the name)")
    sp.add_argument("--detail", help="path to the linked brief "
                                     "(default: ./<name>-brief.md)")
    sp.add_argument("--owner", help="default @owner for the first task")
    sp.add_argument("--section", default="Section 1",
                    help="heading for the first section")
    sp.add_argument("--no-brief", action="store_true",
                    help="do not create the linked brief")
    sp.add_argument("--force", action="store_true", help="overwrite existing")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("check", help="validate files (exit 1 on errors)")
    add_paths(sp)
    sp.add_argument("--strict", action="store_true",
                    help="treat warnings as errors")
    sp.add_argument("--json", action="store_true", help="JSON report")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("status", help="progress report")
    add_paths(sp)
    sp.add_argument("--no-tasks", action="store_true",
                    help="omit the blocked / next-up lists")
    sp.add_argument("--json", action="store_true", help="JSON report")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("sync", help="rewrite the managed progress block")
    add_paths(sp)
    sp.add_argument("-v", "--verbose", action="store_true")
    sp.set_defaults(func=cmd_sync)

    sp = sub.add_parser("list", help="flat, filterable task list")
    add_paths(sp)
    sp.add_argument("--state", action="append", metavar="STATE",
                    help="filter by state (repeatable): todo in-progress done "
                         "blocked cancelled")
    sp.add_argument("--open", action="store_true",
                    help="only todo / in-progress / blocked")
    sp.add_argument("--owner", help="filter by @owner")
    sp.add_argument("--blocked", dest="blocked_only", action="store_true",
                    help="only blocked tasks")
    sp.add_argument("--id", help="filter by task id")
    sp.add_argument("--json", action="store_true", help="JSON output")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("mark", help="change a task state by id")
    sp.add_argument("id", metavar="TASK_ID", help="e.g. T-003")
    sp.add_argument("state",
                    help="todo | in-progress | done | blocked | cancelled")
    add_paths(sp)
    sp.add_argument("--file", help="restrict to one .tmda file")
    sp.add_argument("--no-sync", action="store_true",
                    help="leave the progress block untouched")
    sp.set_defaults(func=cmd_mark)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
