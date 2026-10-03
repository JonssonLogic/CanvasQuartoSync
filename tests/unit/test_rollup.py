"""A rollup marks one assignment on the strength of several others.

Two things here are worth more than the rest.

The first is that a requirement graded ``pass_fail`` and worth **0 points** has
a *score* of 0 even when the student passed. Reading the score alone would mark
every complete student as failed, and the tool would report "0 of 120 ready"
with nothing to complain about, because it did exactly what it was told. That
is the failure this module has to be immune to.

The second is that a rollup only ever raises a grade. A student holding a pass
who no longer qualifies is reported and left alone, because a tool that can
silently withdraw a pass is not one anybody should be asked to run.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from handlers.content_utils import save_sync_map
from handlers.rollup import (RollupConfigError, apply_rollup, discover_rollups,
                             evaluate_rollup, restrict_to_students)


TARGET_ID = 500
LAB_IDS = [501, 502]


def _course_dir(tmp_path, requires, pass_at=None, target_type="assignment",
                grading_type="pass_fail", sync=True):
    """A course with two labs and one rollup target that requires them."""
    mod = tmp_path / "01_Labs"
    mod.mkdir()
    for i, name in enumerate(("01_Lab_One.qmd", "02_Lab_Two.qmd")):
        (mod / name).write_text(
            '---\ntitle: "Lab %d"\ncanvas:\n  type: assignment\n  points: 1\n---\n' % (i + 1),
            encoding="utf-8")

    spec = "  rollup:\n    requires:\n"
    for r in requires:
        spec += "      - %s\n" % r
    if pass_at is not None:
        spec += "    pass_at: %s\n" % pass_at

    (mod / "09_Rollup.qmd").write_text(
        '---\ntitle: "Laboratory"\ncanvas:\n  type: %s\n  grading_type: %s\n%s---\n'
        % (target_type, grading_type, spec),
        encoding="utf-8")

    if sync:
        save_sync_map(str(tmp_path), {
            "01_Labs/01_Lab_One.qmd": {"id": LAB_IDS[0]},
            "01_Labs/02_Lab_Two.qmd": {"id": LAB_IDS[1]},
            "01_Labs/09_Rollup.qmd": {"id": TARGET_ID},
        })
    return str(tmp_path)


def _sub(user_id, grade=None, score=None, excused=False):
    return SimpleNamespace(user_id=user_id, grade=grade, score=score, excused=excused)


def _course(lab_subs, target_subs, students=(1, 2, 3), grading_type="pass_fail",
            points_possible=0):
    """A course whose assignments return the submissions handed to it."""
    course = MagicMock()
    course.get_users.return_value = [
        SimpleNamespace(id=uid, name="Student %d" % uid,
                        sortable_name="Student %d" % uid)
        for uid in students
    ] + [SimpleNamespace(id=99, name="Test Student", sortable_name="Student, Test")]

    assignments = {}
    for aid, subs in zip(LAB_IDS, lab_subs):
        a = MagicMock()
        a.get_submissions.return_value = subs
        assignments[aid] = a

    target = MagicMock()
    target.grading_type = grading_type
    target.points_possible = points_possible
    target.get_submissions.return_value = target_subs
    assignments[TARGET_ID] = target

    course.get_assignment.side_effect = lambda aid: assignments[aid]
    course._target = target
    return course


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

class TestDiscovery:

    def test_finds_the_declaration_and_resolves_relative_paths(self, tmp_path):
        root = _course_dir(tmp_path, ["01_Lab_One.qmd", "02_Lab_Two.qmd"])
        rollups = discover_rollups(root)

        assert len(rollups) == 1
        r = rollups[0]
        assert r["name"] == "Laboratory"
        assert r["target"] == "01_Labs/09_Rollup.qmd"
        assert r["target_id"] == TARGET_ID
        assert not r["problems"]
        # Declared relative to the file, reported relative to the content root.
        assert [q["path"] for q in r["requires"]] == [
            "01_Labs/01_Lab_One.qmd", "01_Labs/02_Lab_Two.qmd"]
        assert [q["target_id"] for q in r["requires"]] == LAB_IDS

    def test_pass_at_defaults_to_one(self, tmp_path):
        root = _course_dir(tmp_path, ["01_Lab_One.qmd"])
        assert discover_rollups(root)[0]["pass_at"] == 1

    def test_a_file_without_a_rollup_is_not_one(self, tmp_path):
        root = _course_dir(tmp_path, ["01_Lab_One.qmd"])
        # The two labs carry canvas metadata but no rollup block.
        assert len(discover_rollups(root)) == 1

    def test_missing_requirement_is_a_problem_not_a_crash(self, tmp_path):
        root = _course_dir(tmp_path, ["01_Lab_One.qmd", "99_Nope.qmd"])
        r = discover_rollups(root)[0]
        assert any("99_Nope.qmd" in p for p in r["problems"])
        # Still reported, so a GUI can show what is wrong rather than nothing.
        assert len(r["requires"]) == 2

    def test_requiring_itself_is_a_problem(self, tmp_path):
        root = _course_dir(tmp_path, ["09_Rollup.qmd"])
        r = discover_rollups(root)[0]
        assert any("itself" in p for p in r["problems"])

    def test_unsynced_target_is_a_problem(self, tmp_path):
        root = _course_dir(tmp_path, ["01_Lab_One.qmd"], sync=False)
        r = discover_rollups(root)[0]
        assert r["target_id"] is None
        assert any("never been synced" in p for p in r["problems"])

    def test_ungradeable_target_type_is_a_problem(self, tmp_path):
        root = _course_dir(tmp_path, ["01_Lab_One.qmd"], target_type="page")
        r = discover_rollups(root)[0]
        assert any("cannot grade" in p for p in r["problems"])

    def test_directory_without_nn_prefix_is_not_walked(self, tmp_path):
        root = _course_dir(tmp_path, ["01_Lab_One.qmd"])
        stray = tmp_path / "drafts"
        stray.mkdir()
        (stray / "09_Rollup.qmd").write_text(
            '---\ntitle: "Draft"\ncanvas:\n  type: assignment\n  rollup:\n'
            '    requires:\n      - x.qmd\n---\n', encoding="utf-8")
        # The sync ignores unprefixed directories, so the rollup must too.
        assert len(discover_rollups(root)) == 1


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

class TestEvaluation:

    def test_pass_fail_requirement_worth_zero_points_still_counts(self, tmp_path):
        """The trap: score is 0 for a complete student on a 0-point assignment."""
        root = _course_dir(tmp_path, ["01_Lab_One.qmd", "02_Lab_Two.qmd"])
        rollup = discover_rollups(root)[0]
        course = _course(
            lab_subs=[
                [_sub(1, grade="complete", score=0), _sub(2, grade="incomplete", score=0)],
                [_sub(1, grade="complete", score=0), _sub(2, grade="complete", score=0)],
            ],
            target_subs=[],
            students=(1, 2),
        )

        st = evaluate_rollup(course, rollup)["status"]

        assert st["complete"] == 1
        assert [s["id"] for s in st["to_mark"]] == [1]

    def test_numeric_score_meets_the_threshold(self, tmp_path):
        root = _course_dir(tmp_path, ["01_Lab_One.qmd", "02_Lab_Two.qmd"], pass_at=2)
        rollup = discover_rollups(root)[0]
        course = _course(
            lab_subs=[
                [_sub(1, score=2), _sub(2, score=1)],
                [_sub(1, score=3), _sub(2, score=2)],
            ],
            target_subs=[],
            students=(1, 2),
        )

        st = evaluate_rollup(course, rollup)["status"]

        # Student 2 scored 1 on the first lab, below pass_at.
        assert [s["id"] for s in st["to_mark"]] == [1]

    def test_excused_counts_as_passed(self, tmp_path):
        root = _course_dir(tmp_path, ["01_Lab_One.qmd", "02_Lab_Two.qmd"])
        rollup = discover_rollups(root)[0]
        course = _course(
            lab_subs=[[_sub(1, excused=True)], [_sub(1, grade="complete")]],
            target_subs=[],
            students=(1,),
        )

        assert evaluate_rollup(course, rollup)["status"]["complete"] == 1

    def test_test_student_is_never_considered(self, tmp_path):
        root = _course_dir(tmp_path, ["01_Lab_One.qmd", "02_Lab_Two.qmd"])
        rollup = discover_rollups(root)[0]
        course = _course(
            lab_subs=[[_sub(99, grade="complete")], [_sub(99, grade="complete")]],
            target_subs=[],
            students=(1,),
        )

        st = evaluate_rollup(course, rollup)["status"]

        assert st["students"] == 1          # the real student only
        assert st["to_mark"] == []

    def test_already_marked_students_are_not_marked_twice(self, tmp_path):
        root = _course_dir(tmp_path, ["01_Lab_One.qmd", "02_Lab_Two.qmd"])
        rollup = discover_rollups(root)[0]
        course = _course(
            lab_subs=[[_sub(1, grade="complete")], [_sub(1, grade="complete")]],
            target_subs=[_sub(1, grade="complete")],
            students=(1,),
        )

        st = evaluate_rollup(course, rollup)["status"]

        assert st["already"] == 1
        assert st["to_mark"] == []

    def test_a_pass_without_qualifying_is_a_conflict_not_a_removal(self, tmp_path):
        root = _course_dir(tmp_path, ["01_Lab_One.qmd", "02_Lab_Two.qmd"])
        rollup = discover_rollups(root)[0]
        course = _course(
            lab_subs=[[_sub(1, grade="complete")], [_sub(1, grade="incomplete")]],
            target_subs=[_sub(1, grade="complete")],
            students=(1,),
        )

        st = evaluate_rollup(course, rollup)["status"]

        assert [s["id"] for s in st["conflicts"]] == [1]
        assert st["to_mark"] == []

    def test_missing_counts_say_which_requirement_is_holding_things_up(self, tmp_path):
        root = _course_dir(tmp_path, ["01_Lab_One.qmd", "02_Lab_Two.qmd"])
        rollup = discover_rollups(root)[0]
        course = _course(
            lab_subs=[
                [_sub(1, grade="complete"), _sub(2, grade="complete")],
                [_sub(1, grade="complete")],
            ],
            target_subs=[],
            students=(1, 2),
        )

        st = evaluate_rollup(course, rollup)["status"]

        assert st["missing"]["01_Labs/01_Lab_One.qmd"] == 0
        assert st["missing"]["01_Labs/02_Lab_Two.qmd"] == 1

    def test_requirement_without_a_canvas_id_refuses_rather_than_guessing(self, tmp_path):
        root = _course_dir(tmp_path, ["01_Lab_One.qmd", "02_Lab_Two.qmd"])
        rollup = discover_rollups(root)[0]
        rollup["requires"][1]["target_id"] = None
        course = _course(lab_subs=[[], []], target_subs=[], students=(1,))

        with pytest.raises(RollupConfigError):
            evaluate_rollup(course, rollup)


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------

class TestApply:

    def _evaluated(self, tmp_path, **kw):
        root = _course_dir(tmp_path, ["01_Lab_One.qmd", "02_Lab_Two.qmd"], **kw)
        rollup = discover_rollups(root)[0]
        course = _course(
            lab_subs=[[_sub(1, grade="complete")], [_sub(1, grade="complete")]],
            target_subs=[],
            students=(1,),
            **({"grading_type": kw["grading_type"]} if "grading_type" in kw else {}),
        )
        return course, evaluate_rollup(course, rollup)

    def test_pass_fail_target_is_marked_complete(self, tmp_path):
        course, evaluated = self._evaluated(tmp_path)
        submission = MagicMock()
        course._target.get_submission.return_value = submission

        result = apply_rollup(course, evaluated)

        submission.edit.assert_called_once_with(
            submission={"posted_grade": "complete"})
        assert [s["id"] for s in result["marked"]] == [1]

    def test_points_target_is_marked_with_full_marks(self, tmp_path):
        course, evaluated = self._evaluated(tmp_path, grading_type="points")
        course._target.points_possible = 3
        submission = MagicMock()
        course._target.get_submission.return_value = submission

        apply_rollup(course, evaluated)

        submission.edit.assert_called_once_with(submission={"posted_grade": "3"})

    def test_a_broken_rollup_is_never_applied(self, tmp_path):
        root = _course_dir(tmp_path, ["99_Nope.qmd"])
        rollup = discover_rollups(root)[0]

        with pytest.raises(RollupConfigError):
            apply_rollup(MagicMock(), {**rollup, "status": None})

    def test_one_failure_does_not_stop_the_rest(self, tmp_path):
        root = _course_dir(tmp_path, ["01_Lab_One.qmd", "02_Lab_Two.qmd"])
        rollup = discover_rollups(root)[0]
        course = _course(
            lab_subs=[
                [_sub(1, grade="complete"), _sub(2, grade="complete")],
                [_sub(1, grade="complete"), _sub(2, grade="complete")],
            ],
            target_subs=[],
            students=(1, 2),
        )
        evaluated = evaluate_rollup(course, rollup)

        good = MagicMock()
        course._target.get_submission.side_effect = [
            RuntimeError("Canvas said no"), good]

        result = apply_rollup(course, evaluated)

        assert len(result["failed"]) == 1
        assert len(result["marked"]) == 1


# ---------------------------------------------------------------------------
# Confirmed students
#
# A GUI shows a count, asks, and then runs --apply, which evaluates again. A
# student who qualified in between must not be marked without being shown.
# ---------------------------------------------------------------------------

class TestRestrictToStudents:

    def _evaluated(self, tmp_path):
        root = _course_dir(tmp_path, ["01_Lab_One.qmd", "02_Lab_Two.qmd"])
        rollup = discover_rollups(root)[0]
        both = [_sub(1, grade="complete"), _sub(2, grade="complete")]
        course = _course(lab_subs=[both, both], target_subs=[], students=(1, 2))
        return course, evaluate_rollup(course, rollup)

    def test_a_student_who_qualified_after_confirming_is_not_marked(self, tmp_path):
        course, evaluated = self._evaluated(tmp_path)
        assert [s["id"] for s in evaluated["status"]["to_mark"]] == [1, 2]

        narrowed, skipped = restrict_to_students(evaluated, [1])

        assert [s["id"] for s in narrowed["status"]["to_mark"]] == [1]
        assert skipped == []
        course._target.get_submission.return_value = MagicMock()
        result = apply_rollup(course, narrowed)
        assert [s["id"] for s in result["marked"]] == [1]

    def test_a_confirmed_student_no_longer_to_mark_is_reported(self, tmp_path):
        _, evaluated = self._evaluated(tmp_path)

        narrowed, skipped = restrict_to_students(evaluated, [1, 7])

        assert [s["id"] for s in narrowed["status"]["to_mark"]] == [1]
        assert skipped == [7]

    def test_the_original_is_left_alone(self, tmp_path):
        _, evaluated = self._evaluated(tmp_path)

        restrict_to_students(evaluated, [])

        assert len(evaluated["status"]["to_mark"]) == 2

    def test_a_rollup_without_status_passes_through(self):
        broken = {"name": "x", "status": None}

        assert restrict_to_students(broken, [1]) == (broken, [])
