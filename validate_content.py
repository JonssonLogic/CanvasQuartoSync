"""
Offline validation for CanvasQuartoSync content.

Checks local content files without touching Canvas: no credentials, no network,
no Quarto. Intended as a fast feedback loop while authoring - especially for an
AI assistant working in a content folder, which needs to know *before* a sync
whether what it wrote will actually sync, and as what.

Usage:
    python validate_content.py 01_Introduction/02_Welcome.qmd
    python validate_content.py .                 # whole content root

Exit code is 1 if any ERROR was reported, else 0.

The CANVAS_SCHEMA dict below is the single source of truth for which
``canvas.*`` keys exist for each content type. The kit reference, the user
guide, and tests/unit/test_doc_consistency.py are all checked against it.
"""

import argparse
import datetime
import difflib
import json
import os
import re
import sys
from dataclasses import dataclass, field

import frontmatter

from handlers.content_utils import is_valid_name
from handlers.single_sync import build_handlers


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Key:
    """One ``canvas.*`` key: its value kind and, where closed, its choices."""
    kind: str                    # bool | int | number | str | list | date | dict
    choices: tuple = ()
    note: str = ""


GRADING_TYPES = ("points", "percentage", "pass_fail", "letter_grade", "gpa_scale", "not_graded")
SUBMISSION_TYPES = (
    "online_upload", "online_text_entry", "online_url", "media_recording",
    "student_annotation", "none", "external_tool", "on_paper",
)

# Keys every module item shares.
_COMMON = {
    "type": Key("str"),
    "published": Key("bool"),
    "indent": Key("int", note="0-5"),
}

# Settings shared by both quiz engines (same YAML keys; the tool translates).
_QUIZ_COMMON = {
    **_COMMON,
    "title": Key("str"),
    "due_at": Key("date"),
    "unlock_at": Key("date"),
    "lock_at": Key("date"),
    "shuffle_answers": Key("bool"),
    "allowed_attempts": Key("int", note="1 = single, -1 = unlimited"),
    "time_limit": Key("int"),
    "one_question_at_a_time": Key("bool"),
    "cant_go_back": Key("bool"),
    "access_code": Key("str"),
}

RESULT_VIEW_KEYS = {
    "restricted": Key("bool"),
    "show_questions": Key("bool"),
    "show_student_responses": Key("bool"),
    "show_responses_frequency": Key(
        "str", ("always", "once_per_attempt", "after_last_attempt", "once_after_last_attempt")),
    "show_responses_at": Key("date"),
    "hide_responses_at": Key("date"),
    "show_correctness": Key("bool"),
    "show_correctness_at": Key("date"),
    "hide_correctness_at": Key("date"),
    "show_correct_answers": Key("bool"),
    "show_feedback": Key("bool"),
    "show_points_awarded": Key("bool"),
    "show_points_possible": Key("bool"),
}

PDF_KEYS = {
    "target_module": Key("str"),
    "filename": Key("str"),
    "title": Key("str"),
    "published": Key("bool"),
}

CANVAS_SCHEMA = {
    "page": {
        **_COMMON,
        "front_page": Key("bool"),
    },
    "assignment": {
        **_COMMON,
        "points": Key("number"),
        "due_at": Key("date"),
        "unlock_at": Key("date"),
        "lock_at": Key("date"),
        "grading_type": Key("str", GRADING_TYPES),
        "submission_types": Key("list", SUBMISSION_TYPES),
        "allowed_extensions": Key("list"),
        "omit_from_final_grade": Key("bool"),
        "hide_in_gradebook": Key("bool"),
        "group_assignment": Key("bool"),
        "group_set": Key("str"),
    },
    "study_guide": {
        **_COMMON,
        "front_page": Key("bool"),
        "preprocess": Key("bool"),
        "pdf": Key("dict"),
    },
    "subheader": {
        **_COMMON,
    },
    "external_url": {
        **_COMMON,
        "url": Key("str"),
        "new_tab": Key("bool"),
    },
    "quiz": {
        **_QUIZ_COMMON,
        "quiz_type": Key("str", ("practice_quiz", "assignment", "graded_survey", "survey")),
        "description": Key("str"),
        "description_file": Key("str"),
        "show_correct_answers": Key("bool"),
        "omit_from_final_grade": Key("bool"),
    },
    "new_quiz": {
        **_QUIZ_COMMON,
        "quiz_engine": Key("str", ("new",)),
        "points": Key("number"),
        "instructions": Key("str"),
        "shuffle_questions": Key("bool"),
        "calculator_type": Key("str", ("none", "basic", "scientific")),
        "score_to_keep": Key("str", ("highest", "latest", "average", "first")),
        "cooling_period_seconds": Key("int"),
        "grading_type": Key("str", GRADING_TYPES),
        "omit_from_final_grade": Key("bool"),
        "hide_in_gradebook": Key("bool"),
        "result_view": Key("dict"),
    },
}

# Content types whose title comes from ``canvas.title`` rather than the
# top-level frontmatter ``title:``.
_CANVAS_TITLE_TYPES = ("quiz", "new_quiz")

# Settings a type genuinely does not support, but that an author would
# reasonably reach for. Saying why - and what to use instead - is worth more
# than the generic "unknown setting" warning.
UNSUPPORTED_KEYS = {
    "quiz": {
        "hide_in_gradebook": (
            "not available on classic quizzes. Canvas only allows hiding something "
            "worth 0 points, and a classic quiz takes its points from its questions - "
            "so it would only qualify with every question worth 0. Use "
            "'quiz_type: practice_quiz' for a quiz that should stay out of the "
            "gradebook entirely, or switch to 'type: new_quiz'."
        ),
    },
}

QUESTION_TYPES = (
    "multiple_choice_question", "true_false_question", "short_answer_question",
    "fill_in_multiple_blanks_question", "multiple_answers_question",
    "multiple_dropdowns_question", "matching_question", "numerical_question",
    "numeric_question", "calculated_question", "formula_question",
    "essay_question", "file_upload_question", "text_only_question",
)

# Question types that must carry at least one answer.
_NEEDS_ANSWERS = (
    "multiple_choice_question", "true_false_question", "multiple_answers_question",
    "numeric_question", "numerical_question", "short_answer_question",
)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

ERROR = "ERROR"
WARN = "WARN"


@dataclass
class Issue:
    level: str
    message: str


@dataclass
class FileReport:
    path: str
    kind: str = "unknown"          # what this file will sync as
    issues: list = field(default_factory=list)

    def error(self, msg):
        self.issues.append(Issue(ERROR, msg))

    def warn(self, msg):
        self.issues.append(Issue(WARN, msg))

    @property
    def errors(self):
        return [i for i in self.issues if i.level == ERROR]

    @property
    def warnings(self):
        return [i for i in self.issues if i.level == WARN]


# ---------------------------------------------------------------------------
# Handler detection
# ---------------------------------------------------------------------------

_HANDLER_TYPES = {
    "StudyGuideHandler": "study_guide",
    "PageHandler": "page",
    "AssignmentHandler": "assignment",
    "NewQuizHandler": "new_quiz",
    "QuizHandler": "quiz",
    "ExternalLinkHandler": "external_url",
    "SubHeaderHandler": "subheader",
}

_SKIP_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".yml", ".toml"}


def detect_kind(file_path, handlers=None):
    """Return the content type the sync would treat this file as.

    Mirrors the real dispatch: the first handler whose ``can_handle()`` returns
    True wins. Anything unclaimed with an ``NN_`` prefix inside a module becomes
    a solo file upload.
    """
    handlers = handlers or build_handlers()
    for handler in handlers:
        try:
            if handler.can_handle(file_path):
                return _HANDLER_TYPES.get(type(handler).__name__, "unknown")
        except Exception:
            continue
    if os.path.basename(file_path).lower() == "schedule.yaml":
        return "calendar"
    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".qmd", ".json", ".md"):
        return "unclaimed"
    return "file"


# ---------------------------------------------------------------------------
# Value checking
# ---------------------------------------------------------------------------

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:?\d{2})?)?$")


def _configured_zone(content_root):
    """The timezone declared in config.toml, or None.

    The validator runs offline, so only the local declaration is visible - a
    course that relies on the Canvas course timezone simply gets no DST check
    here. That is deliberate: warning about every naive time would fire
    constantly for the intended workflow.
    """
    if not content_root:
        return None
    try:
        from handlers.config import load_config
        from handlers.dates import get_zone
        return get_zone(load_config(content_root).get("timezone"))
    except Exception:
        return None


# Keys where a bare date (meaning midnight) is very likely a mistake: a deadline
# at 00:00 falls at the *start* of the day, not the end. `unlock_at` is left out -
# "available from the 17th" genuinely does mean midnight.
_END_OF_DAY_KEYS = {"due_at", "lock_at"}


def _check_midnight(report, dotted_name, value):
    """Warn when a deadline is written as a bare date, which means 00:00."""
    if dotted_name not in _END_OF_DAY_KEYS:
        return
    if isinstance(value, datetime.datetime):
        return
    if isinstance(value, datetime.date):
        bare = True
    else:
        bare = isinstance(value, str) and len(value.strip()) == 10
    if bare:
        report.warn(
            f"canvas.{dotted_name}: {value} means midnight (00:00), so this falls at "
            f"the START of that day. For an end-of-day deadline write "
            f"'{str(value).strip()}T23:59:00'."
        )


def _check_dst(report, dotted_name, value, tz):
    """Warn about local times daylight saving makes non-existent or ambiguous.

    Advisory: both still resolve to some instant, so this never fails a file.
    """
    if tz is None:
        return
    try:
        from handlers.dates import dst_anomaly, naive_local
        flag = dst_anomaly(naive_local(value), tz)
    except Exception:
        return
    if flag == "gap":
        report.warn(
            f"canvas.{dotted_name}: {value} never happens - the clocks jump forward "
            f"over that hour when daylight saving starts. Canvas will store a shifted time."
        )
    elif flag == "ambiguous":
        report.warn(
            f"canvas.{dotted_name}: {value} happens twice - the clocks go back that "
            f"night. The earlier of the two is used."
        )


def _check_value(report, dotted_name, key, value, tz=None):
    """Validate one value against its Key spec."""
    if value is None:
        return

    if key.kind == "bool" and not isinstance(value, bool):
        report.error(f"canvas.{dotted_name}: expected true/false, got {value!r}")
        return
    if key.kind == "int" and (isinstance(value, bool) or not isinstance(value, int)):
        report.error(f"canvas.{dotted_name}: expected a whole number, got {value!r}")
        return
    if key.kind == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        report.error(f"canvas.{dotted_name}: expected a number, got {value!r}")
        return
    if key.kind == "str" and not isinstance(value, str):
        report.error(f"canvas.{dotted_name}: expected text, got {value!r}")
        return
    if key.kind == "list" and not isinstance(value, list):
        report.error(f"canvas.{dotted_name}: expected a list, got {value!r}")
        return
    if key.kind == "dict" and not isinstance(value, dict):
        report.error(f"canvas.{dotted_name}: expected a nested block, got {value!r}")
        return
    if key.kind == "date":
        if isinstance(value, (datetime.date, datetime.datetime)):
            _check_midnight(report, dotted_name, value)
            _check_dst(report, dotted_name, value, tz)
            return
        if not isinstance(value, str) or not _ISO_RE.match(value.strip()):
            report.error(
                f"canvas.{dotted_name}: expected an ISO 8601 date "
                f"(e.g. 2026-03-15T23:59:00Z), got {value!r}"
            )
            return
        _check_midnight(report, dotted_name, value)
        _check_dst(report, dotted_name, value, tz)

    if key.choices:
        values = value if isinstance(value, list) else [value]
        for v in values:
            if v not in key.choices:
                report.error(
                    f"canvas.{dotted_name}: {v!r} is not valid. "
                    f"Choose from: {', '.join(key.choices)}"
                )

    if dotted_name == "indent" and isinstance(value, int) and not 0 <= value <= 5:
        report.error(f"canvas.indent: must be between 0 and 5, got {value}")


def _check_keys(report, meta, schema, prefix="", tz=None):
    """Check every key in a canvas metadata block against a schema."""
    for name, value in meta.items():
        dotted = f"{prefix}{name}"
        key = schema.get(name)
        if key is None:
            # A known-but-unsupported setting gets its own explanation instead of
            # the generic warning, and only one message either way.
            unsupported = {} if prefix else UNSUPPORTED_KEYS.get(report.kind, {})
            if name in unsupported:
                report.error(f"canvas.{dotted}: {unsupported[name]}")
                continue
            suggestion = difflib.get_close_matches(name, list(schema), n=1)
            hint = f" Did you mean '{suggestion[0]}'?" if suggestion else ""
            report.warn(f"canvas.{dotted}: unknown setting - it will be ignored.{hint}")
            continue
        _check_value(report, dotted, key, value, tz=tz)

        if key.kind == "dict" and isinstance(value, dict):
            nested = PDF_KEYS if name == "pdf" else RESULT_VIEW_KEYS if name == "result_view" else None
            if nested:
                _check_keys(report, value, nested, prefix=f"{dotted}.", tz=tz)


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

def _check_naming(report, file_path, content_root, has_canvas_meta):
    """Flag files that carry canvas metadata but would be skipped by the sync."""
    filename = os.path.basename(file_path)
    if not has_canvas_meta:
        return

    if not is_valid_name(filename):
        report.error(
            f"'{filename}' has canvas metadata but no NN_ prefix - the sync will "
            f"ignore it. Rename it to e.g. '01_{filename}'."
        )

    parent = os.path.dirname(os.path.abspath(file_path))
    if content_root and os.path.abspath(parent) != os.path.abspath(content_root):
        parent_name = os.path.basename(parent)
        if not is_valid_name(parent_name):
            report.error(
                f"parent folder '{parent_name}' has no NN_ prefix - the sync will "
                f"not walk into it, so this file is unreachable."
            )


# ---------------------------------------------------------------------------
# Links & assets
# ---------------------------------------------------------------------------

_IMAGE_RE = re.compile(r"!\[(.*?)\]\((.*?)\)")
_LINK_RE = re.compile(r"(?<!\!)\[(.*?)\]\((.*?)\)")
_EXTERNAL_PREFIXES = ("http://", "https://", "data:", "#", "mailto:")


def _check_links(report, body, base_path):
    """Confirm local link and image targets exist on disk."""
    # Fenced code blocks are not processed by the sync, so skip them here too.
    body = re.sub(r"```[\s\S]*?```", "", body)

    for label, target in _IMAGE_RE.findall(body):
        if not target or target.startswith(_EXTERNAL_PREFIXES):
            continue
        if not os.path.exists(os.path.normpath(os.path.join(base_path, target))):
            report.error(f"image not found: {target}")

    for label, target in _LINK_RE.findall(body):
        if not target or target.startswith(_EXTERNAL_PREFIXES):
            continue
        abs_target = os.path.normpath(os.path.join(base_path, target))
        if not os.path.exists(abs_target):
            report.error(f"link target not found: {target}")
            continue
        if os.path.splitext(target)[1].lower() in (".qmd", ".json"):
            _check_cross_link(report, abs_target, target)


def _check_cross_link(report, abs_target, shown):
    """A .qmd/.json link resolves to a Canvas item only if it has canvas metadata."""
    try:
        if abs_target.lower().endswith(".json"):
            with open(abs_target, "r", encoding="utf-8") as f:
                data = json.load(f)
            has_meta = bool(data.get("canvas")) or "questions" in data
        else:
            has_meta = bool(frontmatter.load(abs_target).metadata.get("canvas"))
    except Exception:
        return
    if not has_meta:
        report.warn(
            f"'{shown}' has no canvas metadata, so this link uploads it as a "
            f"downloadable file rather than linking to a Canvas item."
        )


# ---------------------------------------------------------------------------
# Quizzes
# ---------------------------------------------------------------------------

def _check_quiz(report, file_path, kind, canvas_meta, raw_text=None):
    """Validate quiz questions using the real parser the sync uses."""
    from handlers.qmd_quiz_parser import parse_qmd_quiz, _extract_question_blocks

    if file_path.lower().endswith(".json"):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            report.error(f"invalid JSON: {e}")
            return
        questions = data.get("questions", data if isinstance(data, list) else [])
    else:
        try:
            _, questions = parse_qmd_quiz(raw_text)
        except Exception as e:
            report.error(f"quiz could not be parsed: {e}")
            return

        # Mixing checklist and div answers silently drops the checklist ones.
        for attrs, block in _extract_question_blocks(raw_text):
            has_checklist = re.search(r"^\s*-\s*\[[ xX]\]", block, re.MULTILINE)
            has_divs = re.search(r"^:::+\s*\{\.answer", block, re.MULTILINE)
            if has_checklist and has_divs:
                report.error(
                    "a question mixes checklist answers (- [x]) with ::: {.answer} "
                    "blocks - use one style per question; the checklist answers "
                    "would be dropped."
                )

    if not questions:
        report.error("quiz has no questions.")
        return

    if kind == "new_quiz" and canvas_meta.get("hide_in_gradebook"):
        # A New Quiz's gradebook points come from its items, not just the
        # `points` key - and questions default to 1 point each. The hide is
        # applied while the quiz is still empty, so Canvas accepts it and the
        # items push the points up afterwards, leaving a state it would have
        # refused outright.
        item_points = sum(float(q.get("points_possible") or 0) for q in questions)
        if item_points:
            report.error(
                f"hide_in_gradebook needs the quiz to be worth 0, but its questions "
                f"total {item_points:g} points (each defaults to 1). Add "
                f"points_possible=\"0\" to every question, or drop hide_in_gradebook."
            )

    for i, q in enumerate(questions, start=1):
        label = q.get("question_name") or f"question {i}"
        q_type = q.get("question_type", "multiple_choice_question")

        if q_type not in QUESTION_TYPES:
            report.error(f"{label}: unknown question type '{q_type}'.")
            continue

        if kind == "quiz" and q_type in ("numeric_question", "formula_question"):
            report.error(
                f"{label}: '{q_type}' is only supported on the New Quizzes engine. "
                f"Set canvas.type to new_quiz."
            )
            continue

        answers = q.get("answers") or []
        correct = [a for a in answers
                   if a.get("answer_weight") == 100 or a.get("weight") == 100]

        if q_type in _NEEDS_ANSWERS and not answers:
            report.error(f"{label}: '{q_type}' needs at least one answer.")
        elif q_type in ("multiple_choice_question", "true_false_question"):
            if len(correct) != 1:
                report.error(
                    f"{label}: '{q_type}' needs exactly one correct answer, found "
                    f"{len(correct)}."
                )
        elif q_type == "multiple_answers_question" and not correct:
            report.error(f"{label}: needs at least one correct answer.")

        if q_type == "formula_question":
            _check_formula(report, label, q)


def _check_formula(report, label, q):
    """Evaluate a formula question the way the sync will, so bad math fails here."""
    if not q.get("formula"):
        report.error(f"{label}: formula question needs a ::: {{.formula}} block.")
        return
    variables = q.get("variables") or []
    if not variables:
        report.error(f"{label}: formula question needs at least one ::: {{.variable}} block.")
        return

    declared = {v.get("name") for v in variables if v.get("name")}
    used = set(re.findall(r"\[([A-Za-z_]\w*)\]", q.get("question_text", "")))
    for name in sorted(used - declared):
        report.warn(f"{label}: [{name}] in the text has no matching .variable block.")

    try:
        from handlers.new_quiz_handler import NewQuizHandler
        NewQuizHandler()._generate_formula_solutions(
            str(q["formula"]), variables, 3, str(q.get("distribution", "random"))
        )
    except ImportError as e:
        report.warn(f"{label}: could not evaluate formula ({e}).")
    except Exception as e:
        report.error(f"{label}: formula does not evaluate - {e}")


# ---------------------------------------------------------------------------
# Per-file validation
# ---------------------------------------------------------------------------

def validate_file(file_path, content_root=None, handlers=None):
    """Validate one content file. Returns a FileReport."""
    report = FileReport(path=file_path)
    ext = os.path.splitext(file_path)[1].lower()
    base_path = os.path.dirname(os.path.abspath(file_path))

    if ext in _SKIP_EXTENSIONS:
        report.kind = "file"
        return report

    raw_text = None
    canvas_meta = {}
    root_meta = {}

    if ext in (".qmd", ".md"):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                raw_text = f.read()
            post = frontmatter.loads(raw_text)
            root_meta = post.metadata or {}
            canvas_meta = root_meta.get("canvas") or {}
        except Exception as e:
            report.error(f"frontmatter could not be parsed: {e}")
            return report
        if not isinstance(canvas_meta, dict):
            report.error("'canvas:' must be a nested block of settings.")
            return report
    elif ext == ".json":
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            report.error(f"invalid JSON: {e}")
            return report
        canvas_meta = (data.get("canvas") or {}) if isinstance(data, dict) else {}

    report.kind = detect_kind(file_path, handlers)
    _check_naming(report, file_path, content_root, bool(canvas_meta))

    if report.kind == "unclaimed":
        declared = canvas_meta.get("type")
        if declared:
            report.error(
                f"canvas.type '{declared}' is not recognised. Valid types: "
                f"{', '.join(sorted(CANVAS_SCHEMA))}."
            )
        elif ext in (".qmd", ".md") and is_valid_name(os.path.basename(file_path)):
            # Only NN_-prefixed files are walked by the sync; without a canvas
            # type they fall through to the solo-asset upload path.
            report.warn(
                "no canvas.type - this file uploads as a downloadable attachment, "
                "not a Canvas page. Add 'canvas:' with a 'type:' if that wasn't intended."
            )
        return report

    schema = CANVAS_SCHEMA.get(report.kind)
    if schema:
        _check_keys(report, canvas_meta, schema, tz=_configured_zone(content_root))

        if report.kind == "external_url" and not canvas_meta.get("url"):
            report.error("external_url needs 'canvas.url'.")

        # Title lives in different places depending on the type.
        if report.kind in _CANVAS_TITLE_TYPES:
            if root_meta.get("title") and not canvas_meta.get("title"):
                report.warn(
                    "for quizzes the title goes under 'canvas.title'; the top-level "
                    "'title:' is ignored and the filename would be used instead."
                )
        elif canvas_meta.get("title"):
            report.warn(
                f"'{report.kind}' takes its title from the top-level 'title:', not "
                f"'canvas.title'."
            )

        if canvas_meta.get("hide_in_gradebook") and canvas_meta.get("points"):
            report.error(
                "hide_in_gradebook requires points to be 0 or unset - Canvas rejects "
                "it otherwise, so the sync will skip the setting and leave this "
                "visible in the gradebook."
            )
        if (report.kind == "quiz" and "omit_from_final_grade" in canvas_meta
                and canvas_meta.get("quiz_type") not in ("assignment", "graded_survey")):
            report.warn(
                "omit_from_final_grade only applies to graded quizzes - practice "
                "quizzes and ungraded surveys never reach the gradebook, so there is "
                "nothing to omit. Set quiz_type: assignment or graded_survey."
            )
        if canvas_meta.get("cant_go_back") and not canvas_meta.get("one_question_at_a_time"):
            report.warn("cant_go_back has no effect without one_question_at_a_time: true.")

    if report.kind in ("quiz", "new_quiz"):
        _check_quiz(report, file_path, report.kind, canvas_meta, raw_text)
        desc = canvas_meta.get("description_file")
        if desc:
            desc_path = os.path.join(base_path, desc)
            if not os.path.exists(desc_path):
                report.error(f"description_file not found: {desc}")
            elif is_valid_name(os.path.basename(desc)):
                report.warn(
                    f"description_file '{desc}' has an NN_ prefix, so it will also "
                    f"sync as its own page. Remove the prefix."
                )

    if raw_text is not None:
        _check_links(report, frontmatter.loads(raw_text).content, base_path)

    return report


# ---------------------------------------------------------------------------
# Walking
# ---------------------------------------------------------------------------

_IGNORED_DIRS = {".git", ".claude", "__pycache__", ".canvas_snapshots", "assets", "graphics"}
_IGNORED_FILES = {
    ".canvas_sync_map.json", "_quarto.yml", "config.toml",
    "CLAUDE.md", "README.md",          # kit / project docs, not course content
}
_CHECKABLE = {".qmd", ".md", ".json"}


def validate_path(target, content_root=None):
    """Validate a single file or an entire content directory."""
    target = os.path.abspath(target)
    handlers = build_handlers()

    if os.path.isfile(target):
        root = content_root or _guess_content_root(target)
        return [validate_file(target, root, handlers)]

    root = content_root or target
    reports = []
    for dirpath, dirnames, filenames in os.walk(target):
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS]
        for name in sorted(filenames):
            if os.path.splitext(name)[1].lower() not in _CHECKABLE:
                continue
            if name in _IGNORED_FILES or name.startswith(("_temp_", "tmp-")):
                continue
            reports.append(validate_file(os.path.join(dirpath, name), root, handlers))
    return reports


def _guess_content_root(file_path):
    """Walk up from a file to the folder holding config.toml / course_id.txt."""
    d = os.path.dirname(os.path.abspath(file_path))
    for _ in range(4):
        if any(os.path.exists(os.path.join(d, m))
               for m in ("config.toml", "course_id.txt", ".canvas_sync_map.json")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return os.path.dirname(os.path.abspath(file_path))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def render(reports, base=None, show_clean=True):
    """Format reports as plain text. Returns (text, error_count)."""
    lines = []
    errors = warnings = 0

    for r in reports:
        shown = os.path.relpath(r.path, base) if base else r.path
        if not r.issues:
            if show_clean:
                lines.append(f"  OK    {shown}  [{r.kind}]")
            continue
        lines.append(f"        {shown}  [{r.kind}]")
        for issue in r.issues:
            lines.append(f"  {issue.level:<5} {issue.message}")
            if issue.level == ERROR:
                errors += 1
            else:
                warnings += 1

    summary = f"{len(reports)} file(s) checked, {errors} error(s), {warnings} warning(s)"
    lines.append("")
    lines.append(summary)
    return "\n".join(lines), errors


def main():
    parser = argparse.ArgumentParser(
        description="Validate CanvasQuartoSync content offline (no Canvas needed)."
    )
    parser.add_argument("target", nargs="?", default=".",
                        help="File or content directory to check (default: current dir).")
    parser.add_argument("--content-root",
                        help="Content root, if it can't be inferred from the target.")
    parser.add_argument("--errors-only", action="store_true",
                        help="Hide files that passed cleanly.")
    args = parser.parse_args()

    if not os.path.exists(args.target):
        print(f"Not found: {args.target}")
        return 2

    reports = validate_path(args.target, args.content_root)
    base = args.target if os.path.isdir(args.target) else os.path.dirname(os.path.abspath(args.target))
    text, errors = render(reports, base=base, show_clean=not args.errors_only)
    print(text)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
