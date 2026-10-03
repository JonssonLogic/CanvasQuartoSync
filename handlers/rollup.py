"""Derive one assignment's grade from several others.

A student records system usually wants **one** result per examination module,
while teaching wants **several** assignments that are examined separately. The
Swedish system LADOK is the case this was written for: its "Laboration 3 hp"
module maps to a single Canvas column, but the course runs five labs that
students demonstrate one at a time, on their own schedule.

A *rollup* closes that gap. One assignment is declared to be the roll-up of
several others, and this module works out which students have satisfied the
requirement and marks the roll-up assignment for them. Nothing else is graded,
and nothing is ever taken away.

The rule lives in the **target's own frontmatter**, so it sits next to the
assignment it governs and travels with it through a rename::

    ---
    title: "Laboration"
    canvas:
      type: assignment
      grading_type: pass_fail
      rollup:
        requires:
          - 01_Dragprovning.qmd
          - 02_Balklosare.qmd
        pass_at: 1
    ---

Paths in ``requires`` are relative to the declaring file, the same way links in
the body are. There is no central registry: a course may declare as many
rollups as it likes, each in its own target file, and :func:`discover_rollups`
finds them by reading frontmatter.

Nothing here writes to Canvas except :func:`apply_rollup`, and the caller has
to ask for that explicitly.
"""

import json
import os

import frontmatter as fm

from handlers.log import logger
from handlers.content_utils import load_sync_map, is_valid_name


# Extensions that can carry a rollup declaration. A rollup target is always a
# graded Canvas object, so the quiz JSON form is included.
_DECLARABLE = ('.qmd', '.md', '.json')

# What a rollup writes when the target is not pass_fail: full marks. A rollup
# answers a yes/no question, so a partial score would be an invention.
_PASS_FAIL_GRADE = 'complete'


class RollupConfigError(Exception):
    """A rollup declaration that cannot be acted on."""


def _rel(content_root: str, path: str) -> str:
    return os.path.relpath(path, content_root).replace('\\', '/')


def _iter_content_files(content_root: str):
    """Yield (abs_path, rel_path) for every file the sync would look at.

    Mirrors the sync's own rule: only ``NN_`` directories are walked, because
    a directory without the prefix is never module content. Files at the
    content root are included, since those sync too (outside any module).
    """
    for fname in sorted(os.listdir(content_root)):
        fpath = os.path.join(content_root, fname)
        if os.path.isfile(fpath) and fname.endswith(_DECLARABLE):
            yield fpath, fname

    for entry in sorted(os.listdir(content_root)):
        mod_dir = os.path.join(content_root, entry)
        if not os.path.isdir(mod_dir) or not is_valid_name(entry):
            continue
        for fname in sorted(os.listdir(mod_dir)):
            fpath = os.path.join(mod_dir, fname)
            if os.path.isfile(fpath) and fname.endswith(_DECLARABLE):
                yield fpath, os.path.join(entry, fname).replace('\\', '/')


def _read_declaration(fpath: str):
    """Return (title, canvas_meta) for a content file, or None if unreadable."""
    try:
        if fpath.endswith('.json'):
            with open(fpath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return None
            return data.get('title', ''), data
        post = fm.load(fpath)
        meta = post.metadata or {}
        canvas_meta = meta.get('canvas') or {}
        if not isinstance(canvas_meta, dict):
            return None
        return meta.get('title', ''), canvas_meta
    except Exception:
        return None


def discover_rollups(content_root: str) -> list:
    """Find every rollup declared in the course, without touching Canvas.

    Returns a list of dicts, one per declaration, each carrying its own
    ``problems`` list. A declaration with problems is still returned: the
    point of this function is to *report* a broken rule, not to hide it.

    Each dict has::

        name        the target's title, or its filename when it has none
        target      rel path of the file declaring the rollup
        target_id   Canvas id from the sync map, or None if never synced
        requires    [{path, exists, target_id, title}, ...]
        pass_at     score at or above which a requirement counts as passed
        grading_type  the target's grading type, as declared
        points        the target's points_possible, as declared
        problems    [str, ...]  empty when the rule is usable
    """
    sync_map = load_sync_map(content_root)

    def canvas_id(rel_path):
        entry = sync_map.get(rel_path)
        return entry.get('id') if isinstance(entry, dict) else None

    rollups = []
    for fpath, rel_path in _iter_content_files(content_root):
        decl = _read_declaration(fpath)
        if decl is None:
            continue
        title, canvas_meta = decl
        spec = canvas_meta.get('rollup')
        if not spec:
            continue

        problems = []
        if not isinstance(spec, dict):
            problems.append("rollup: must be a block with a 'requires' list")
            spec = {}

        ctype = canvas_meta.get('type')
        if ctype not in (None, 'assignment', 'quiz', 'new_quiz'):
            problems.append(
                f"rollup target is type '{ctype}', which Canvas cannot grade")

        raw_requires = spec.get('requires') or []
        if not isinstance(raw_requires, list) or not raw_requires:
            problems.append("rollup.requires must be a non-empty list of paths")
            raw_requires = []

        base = os.path.dirname(fpath)
        requires = []
        for item in raw_requires:
            if not isinstance(item, str):
                problems.append(f"rollup.requires entry is not a path: {item!r}")
                continue
            abs_req = os.path.normpath(os.path.join(base, item))
            rel_req = _rel(content_root, abs_req)
            exists = os.path.isfile(abs_req)
            if not exists:
                problems.append(f"required file not found: {item}")
            elif os.path.normcase(abs_req) == os.path.normcase(fpath):
                problems.append(f"rollup requires itself: {item}")
            requires.append({
                'path': rel_req,
                'declared_as': item,
                'exists': exists,
                'target_id': canvas_id(rel_req),
                'title': (_read_declaration(abs_req) or ('', {}))[0] if exists else '',
            })

        pass_at = spec.get('pass_at', 1)
        if not isinstance(pass_at, (int, float)) or isinstance(pass_at, bool):
            problems.append(f"rollup.pass_at must be a number, got {pass_at!r}")
            pass_at = 1

        tid = canvas_id(rel_path)
        if tid is None:
            problems.append("target has never been synced, so it has no Canvas id")

        rollups.append({
            'name': title or os.path.basename(rel_path),
            'target': rel_path,
            'target_id': tid,
            'requires': requires,
            'pass_at': pass_at,
            'grading_type': canvas_meta.get('grading_type'),
            'points': canvas_meta.get('points'),
            'problems': problems,
        })

    return rollups


def _submission_passed(sub, pass_at) -> bool:
    """Has this submission met the requirement?

    Three shapes have to count as passed, because a course may grade its
    requirements any of these ways:

    * excused, which Canvas represents as a flag rather than a score
    * ``grade == 'complete'`` on a pass_fail assignment, whose *score* is
      ``points_possible`` and therefore ``0`` when the assignment is worth
      nothing. Reading the score alone would mark every complete student as
      failed, which is the trap this function exists to avoid.
    * a numeric score at or above ``pass_at``
    """
    if getattr(sub, 'excused', False):
        return True
    if getattr(sub, 'grade', None) == _PASS_FAIL_GRADE:
        return True
    score = getattr(sub, 'score', None)
    return score is not None and score >= pass_at


def _target_passing_grade(rollup, assignment) -> str:
    """The grade a qualifying student should receive on the target."""
    gtype = rollup.get('grading_type') or getattr(assignment, 'grading_type', None)
    if gtype == 'pass_fail':
        return _PASS_FAIL_GRADE
    points = getattr(assignment, 'points_possible', None)
    if points:
        return str(points)
    # A points assignment worth nothing still needs a mark that reads as done.
    return '1'


def _already_passing(sub, rollup, assignment) -> bool:
    if getattr(sub, 'excused', False):
        return True
    if getattr(sub, 'grade', None) == _PASS_FAIL_GRADE:
        return True
    gtype = rollup.get('grading_type') or getattr(assignment, 'grading_type', None)
    if gtype == 'pass_fail':
        return False
    score = getattr(sub, 'score', None)
    return score is not None and score > 0


def _active_students(course) -> dict:
    """Active student enrolments, keyed by user id.

    Canvas's own "Test Student" is not a person and must never be graded, and
    a student who has left the course should not hold up a report about the
    ones who are still here.
    """
    students = {}
    for u in course.get_users(enrollment_type=['student'],
                              enrollment_state=['active']):
        name = getattr(u, 'name', '') or ''
        if name == 'Test Student':
            continue
        students[u.id] = {
            'id': u.id,
            'name': name,
            'sortable_name': getattr(u, 'sortable_name', '') or name,
        }
    return students


def evaluate_rollup(course, rollup: dict) -> dict:
    """Work out which students satisfy the rollup. Reads only.

    Returns the rollup dict extended with a ``status`` block::

        students     how many active students were considered
        complete     how many satisfy every requirement
        already      how many already hold a passing grade on the target
        to_mark      [{id, name, sortable_name}, ...] qualifying but unmarked
        conflicts    [{...}] holding a passing grade without qualifying
        missing      {rel_path: count} how many students each requirement
                     is still waiting on, which is what a teacher actually
                     wants to know when the number is lower than expected
    """
    result = dict(rollup)
    if rollup['problems']:
        result['status'] = None
        return result

    assignment = course.get_assignment(rollup['target_id'])
    students = _active_students(course)

    # One paginated call per requirement rather than one per student: five
    # requirements and 120 students is 5 requests, not 600.
    passed_by_student = {uid: set() for uid in students}
    missing = {}
    for req in rollup['requires']:
        rid = req['target_id']
        if rid is None:
            raise RollupConfigError(
                f"requirement has never been synced, so it has no Canvas id: {req['path']}")
        for sub in course.get_assignment(rid).get_submissions():
            if sub.user_id not in passed_by_student:
                continue
            if _submission_passed(sub, rollup['pass_at']):
                passed_by_student[sub.user_id].add(req['path'])
        n_missing = sum(1 for uid in students
                        if req['path'] not in passed_by_student[uid])
        missing[req['path']] = n_missing

    needed = {r['path'] for r in rollup['requires']}
    qualifies = {uid for uid in students if needed <= passed_by_student[uid]}

    already, to_mark, conflicts = set(), [], []
    for sub in assignment.get_submissions():
        if sub.user_id not in students:
            continue
        if _already_passing(sub, rollup, assignment):
            already.add(sub.user_id)
            if sub.user_id not in qualifies:
                conflicts.append(students[sub.user_id])

    for uid in sorted(qualifies, key=lambda u: students[u]['sortable_name']):
        if uid not in already:
            to_mark.append(students[uid])

    result['status'] = {
        'students': len(students),
        'complete': len(qualifies),
        'already': len(already),
        'to_mark': to_mark,
        'conflicts': sorted(conflicts, key=lambda s: s['sortable_name']),
        'missing': missing,
    }
    return result


def restrict_to_students(evaluated: dict, student_ids) -> tuple:
    """Narrow ``to_mark`` to the students someone actually confirmed.

    A GUI shows a count, asks, and then calls ``--apply``, which evaluates
    again. A student who qualified in between would be marked without having
    been shown to anyone. Intersecting with the confirmed ids means the write
    can only ever be a subset of what was approved.

    Returns ``(evaluated, skipped)``: the narrowed rollup, and the confirmed
    ids that are no longer on the list (marked meanwhile, or no longer
    qualifying). ``evaluated`` is returned unchanged when it has no status.
    """
    status = evaluated.get('status')
    if status is None:
        return evaluated, []
    wanted = set(student_ids)
    kept = [s for s in status['to_mark'] if s['id'] in wanted]
    kept_ids = {s['id'] for s in kept}
    skipped = sorted(wanted - kept_ids)
    return {**evaluated, 'status': {**status, 'to_mark': kept}}, skipped


def apply_rollup(course, evaluated: dict) -> dict:
    """Mark the qualifying students. The only function here that writes.

    Grades are only ever raised. A student who holds a passing grade without
    qualifying is left exactly as they are and reported as a conflict, because
    a tool that can silently withdraw a pass is not one anybody should run.
    """
    status = evaluated.get('status')
    if status is None:
        raise RollupConfigError(
            f"{evaluated['name']}: rollup has unresolved problems, refusing to apply")

    assignment = course.get_assignment(evaluated['target_id'])
    grade = _target_passing_grade(evaluated, assignment)

    marked, failed = [], []
    for student in status['to_mark']:
        try:
            sub = assignment.get_submission(student['id'])
            sub.edit(submission={'posted_grade': grade})
            marked.append(student)
            logger.info("  marked %s", student['sortable_name'])
        except Exception as e:
            failed.append({**student, 'error': str(e)})
            logger.error("  FAILED %s: %s", student['sortable_name'], e)

    return {
        'target': evaluated['target'],
        'name': evaluated['name'],
        'grade': grade,
        'marked': marked,
        'failed': failed,
        'conflicts': status['conflicts'],
    }
