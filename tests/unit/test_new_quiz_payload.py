"""Tests for NewQuizHandler._build_quiz_payload() — quiz-level settings mapping.

The New Quizzes API nests display/behavior settings inside ``quiz_settings``,
with multiple-attempt fields further nested in ``quiz_settings.multiple_attempts``.
"""

from handlers.new_quiz_handler import NewQuizHandler


handler = NewQuizHandler()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _qs(payload):
    """Shortcut: return the quiz_settings dict (or empty)."""
    return payload.get('quiz_settings', {})


def _ma(payload):
    """Shortcut: return the multiple_attempts dict (or empty)."""
    return _qs(payload).get('multiple_attempts', {})


def _rv(payload):
    """Shortcut: return the result_view_settings dict (or empty)."""
    return _qs(payload).get('result_view_settings', {})


# ---------------------------------------------------------------------------
# Multiple attempts
# ---------------------------------------------------------------------------

class TestAllowedAttempts:

    def test_single_attempt(self):
        payload = handler._build_quiz_payload("Q", False, {'allowed_attempts': 1})
        assert _ma(payload)['multiple_attempts_enabled'] is False
        assert 'max_attempts' not in _ma(payload)
        assert 'score_to_keep' not in _ma(payload)

    def test_unlimited_attempts(self):
        payload = handler._build_quiz_payload("Q", False, {'allowed_attempts': -1})
        assert _ma(payload)['multiple_attempts_enabled'] is True
        assert 'max_attempts' not in _ma(payload)
        assert _ma(payload)['score_to_keep'] == 'highest'

    def test_unlimited_attempts_custom_score_to_keep(self):
        payload = handler._build_quiz_payload("Q", False, {
            'allowed_attempts': -1, 'score_to_keep': 'latest'})
        assert _ma(payload)['score_to_keep'] == 'latest'

    def test_finite_multiple_attempts(self):
        payload = handler._build_quiz_payload("Q", False, {'allowed_attempts': 3})
        assert _ma(payload)['multiple_attempts_enabled'] is True
        assert _ma(payload)['max_attempts'] == 3
        assert _ma(payload)['score_to_keep'] == 'highest'

    def test_no_attempts_key(self):
        payload = handler._build_quiz_payload("Q", False, {})
        assert 'quiz_settings' not in payload


# ---------------------------------------------------------------------------
# Top-level fields
# ---------------------------------------------------------------------------

class TestBasicSettings:

    def test_title_and_published(self):
        payload = handler._build_quiz_payload("My Quiz", True, {})
        assert payload['title'] == "My Quiz"
        assert payload['published'] is True

    def test_points_mapped_to_points_possible(self):
        payload = handler._build_quiz_payload("Q", False, {'points': 10.5})
        assert payload['points_possible'] == 10.5

    def test_dates_with_values(self):
        meta = {
            'due_at': '2025-06-01T23:59:00Z',
            'unlock_at': '2025-05-01T00:00:00Z',
            'lock_at': '2025-06-02T00:00:00Z',
        }
        payload = handler._build_quiz_payload("Q", False, meta)
        assert payload['due_at'] == '2025-06-01T23:59:00Z'
        assert payload['unlock_at'] == '2025-05-01T00:00:00Z'
        assert payload['lock_at'] == '2025-06-02T00:00:00Z'

    def test_dates_with_none_become_empty_string(self):
        meta = {'due_at': None, 'unlock_at': None, 'lock_at': None}
        payload = handler._build_quiz_payload("Q", False, meta)
        assert payload['due_at'] == ''
        assert payload['unlock_at'] == ''
        assert payload['lock_at'] == ''

    def test_instructions(self):
        payload = handler._build_quiz_payload("Q", False, {'instructions': 'Read carefully'})
        assert payload['instructions'] == 'Read carefully'

    def test_omit_from_final_grade_not_in_quiz_payload(self):
        """omit_from_final_grade is an assignment-level setting, not a quiz setting."""
        payload = handler._build_quiz_payload("Q", False, {'omit_from_final_grade': True})
        assert 'omit_from_final_grade' not in payload


# ---------------------------------------------------------------------------
# quiz_settings fields
# ---------------------------------------------------------------------------

class TestQuizDisplaySettings:

    def test_shuffle_answers(self):
        payload = handler._build_quiz_payload("Q", False, {'shuffle_answers': True})
        assert _qs(payload)['shuffle_answers'] is True

    def test_shuffle_questions(self):
        payload = handler._build_quiz_payload("Q", False, {'shuffle_questions': True})
        assert _qs(payload)['shuffle_questions'] is True

    def test_time_limit_sets_flag_and_seconds(self):
        payload = handler._build_quiz_payload("Q", False, {'time_limit': 1800})
        assert _qs(payload)['has_time_limit'] is True
        assert _qs(payload)['session_time_limit_in_seconds'] == 1800


class TestClassicParitySettings:

    def test_one_question_at_a_time_true(self):
        payload = handler._build_quiz_payload("Q", False, {'one_question_at_a_time': True})
        assert _qs(payload)['one_at_a_time_type'] == 'question'

    def test_one_question_at_a_time_false(self):
        payload = handler._build_quiz_payload("Q", False, {'one_question_at_a_time': False})
        assert _qs(payload)['one_at_a_time_type'] == 'none'

    def test_cant_go_back_inverted_to_allow_backtracking(self):
        payload = handler._build_quiz_payload("Q", False, {'cant_go_back': True})
        assert _qs(payload)['allow_backtracking'] is False

    def test_cant_go_back_false_allows_backtracking(self):
        payload = handler._build_quiz_payload("Q", False, {'cant_go_back': False})
        assert _qs(payload)['allow_backtracking'] is True

    def test_access_code_sets_require_flag_and_value(self):
        payload = handler._build_quiz_payload("Q", False, {'access_code': 'secret123'})
        assert _qs(payload)['require_student_access_code'] is True
        assert _qs(payload)['student_access_code'] == 'secret123'


# ---------------------------------------------------------------------------
# New-Quizzes-only settings
# ---------------------------------------------------------------------------

class TestNewQuizOnlySettings:

    def test_score_to_keep_requires_multiple_attempts(self):
        """score_to_keep is only sent when multiple attempts are enabled."""
        payload = handler._build_quiz_payload("Q", False, {
            'allowed_attempts': -1, 'score_to_keep': 'average'})
        assert _ma(payload)['score_to_keep'] == 'average'

    def test_score_to_keep_ignored_for_single_attempt(self):
        """score_to_keep is not sent when single attempt."""
        payload = handler._build_quiz_payload("Q", False, {
            'allowed_attempts': 1, 'score_to_keep': 'average'})
        assert 'score_to_keep' not in _ma(payload)

    def test_cooling_period_seconds(self):
        payload = handler._build_quiz_payload("Q", False, {'cooling_period_seconds': 300})
        assert _ma(payload)['cooling_period_seconds'] == 300

    def test_calculator_type(self):
        payload = handler._build_quiz_payload("Q", False, {'calculator_type': 'scientific'})
        assert _qs(payload)['calculator_type'] == 'scientific'


# ---------------------------------------------------------------------------
# Minimal / empty
# ---------------------------------------------------------------------------

class TestMinimalPayload:

    def test_empty_meta_only_title_and_published(self):
        payload = handler._build_quiz_payload("Minimal", False, {})
        assert payload == {'title': 'Minimal', 'published': False}


# ---------------------------------------------------------------------------
# Nesting structure
# ---------------------------------------------------------------------------

class TestPayloadStructure:

    def test_settings_nested_under_quiz_settings(self):
        """Verify display settings live under quiz_settings, not top-level."""
        payload = handler._build_quiz_payload("Q", False, {
            'shuffle_answers': True,
            'allowed_attempts': -1,
        })
        assert 'shuffle_answers' not in payload
        assert 'multiple_attempts_enabled' not in payload
        assert 'shuffle_answers' in payload['quiz_settings']
        assert 'multiple_attempts' in payload['quiz_settings']

    def test_top_level_fields_not_nested(self):
        """Verify dates/points stay at top level."""
        payload = handler._build_quiz_payload("Q", True, {
            'points': 10,
            'due_at': '2025-06-01T00:00:00Z',
        })
        assert payload['points_possible'] == 10
        assert payload['due_at'] == '2025-06-01T00:00:00Z'
        assert 'quiz_settings' not in payload


# ---------------------------------------------------------------------------
# Result view settings
# ---------------------------------------------------------------------------

class TestResultViewSettings:

    def test_no_result_view_key(self):
        """No result_view in meta → no result_view_settings in payload."""
        payload = handler._build_quiz_payload("Q", False, {})
        assert 'result_view_settings' not in _qs(payload)

    def test_restricted_only(self):
        payload = handler._build_quiz_payload("Q", False, {
            'result_view': {'restricted': True}})
        assert _rv(payload) == {'result_view_restricted': True}

    def test_all_boolean_fields(self):
        meta = {'result_view': {
            'restricted': True,
            'show_questions': True,
            'show_student_responses': True,
            'show_correctness': False,
            'show_correct_answers': False,
            'show_feedback': True,
            'show_points_awarded': True,
            'show_points_possible': False,
        }}
        rv = _rv(handler._build_quiz_payload("Q", False, meta))
        assert rv['result_view_restricted'] is True
        assert rv['display_items'] is True
        assert rv['display_item_response'] is True
        assert rv['display_item_response_correctness'] is False
        assert rv['display_item_correct_answer'] is False
        assert rv['display_item_feedback'] is True
        assert rv['display_points_awarded'] is True
        assert rv['display_points_possible'] is False

    def test_frequency_enum(self):
        payload = handler._build_quiz_payload("Q", False, {
            'result_view': {'show_responses_frequency': 'after_last_attempt'}})
        assert _rv(payload)['display_item_response_qualifier'] == 'after_last_attempt'

    def test_date_fields(self):
        meta = {'result_view': {
            'show_responses_at': '2025-06-01T00:00:00Z',
            'hide_responses_at': '2025-06-15T23:59:00Z',
            'show_correctness_at': '2025-06-01T00:00:00Z',
            'hide_correctness_at': '2025-06-15T23:59:00Z',
        }}
        rv = _rv(handler._build_quiz_payload("Q", False, meta))
        assert rv['show_item_responses_at'] == '2025-06-01T00:00:00Z'
        assert rv['hide_item_responses_at'] == '2025-06-15T23:59:00Z'
        assert rv['show_item_correctness_at'] == '2025-06-01T00:00:00Z'
        assert rv['hide_item_correctness_at'] == '2025-06-15T23:59:00Z'

    def test_partial_settings_only_specified_keys(self):
        """Only keys present in YAML appear in the API payload."""
        payload = handler._build_quiz_payload("Q", False, {
            'result_view': {'restricted': True, 'show_feedback': True}})
        rv = _rv(payload)
        assert len(rv) == 2
        assert rv['result_view_restricted'] is True
        assert rv['display_item_feedback'] is True

    def test_nested_under_quiz_settings(self):
        """result_view_settings lives under quiz_settings, not top-level."""
        payload = handler._build_quiz_payload("Q", False, {
            'result_view': {'restricted': True}})
        assert 'result_view_settings' not in payload
        assert 'result_view_settings' in payload['quiz_settings']

    def test_coexists_with_other_settings(self):
        """result_view_settings + other quiz_settings coexist."""
        payload = handler._build_quiz_payload("Q", False, {
            'shuffle_answers': True,
            'result_view': {'restricted': True},
        })
        assert _qs(payload)['shuffle_answers'] is True
        assert _rv(payload)['result_view_restricted'] is True

    def test_empty_result_view_dict(self):
        """result_view: {} → no result_view_settings key."""
        payload = handler._build_quiz_payload("Q", False, {'result_view': {}})
        assert 'result_view_settings' not in _qs(payload)

    def test_non_dict_result_view_ignored(self):
        """result_view: true (invalid) → gracefully ignored, no crash."""
        payload = handler._build_quiz_payload("Q", False, {'result_view': True})
        assert 'result_view_settings' not in _qs(payload)


# ---------------------------------------------------------------------------
# Backing assignment settings (_update_backing_assignment)
# ---------------------------------------------------------------------------

class TestBackingAssignmentSettings:

    def test_grading_type_defaults_to_points(self):
        """grading_type defaults to 'points' for autograding support."""
        from unittest.mock import MagicMock
        mock_course = MagicMock()
        mock_assignment = MagicMock()
        mock_course.get_assignment.return_value = mock_assignment

        handler._update_backing_assignment(mock_course, '123', {})

        mock_course.get_assignment.assert_called_once_with(123)
        mock_assignment.edit.assert_called_once_with(assignment={'grading_type': 'points'})

    def test_grading_type_custom_value(self):
        """User-specified grading_type overrides the default."""
        from unittest.mock import MagicMock
        mock_course = MagicMock()
        mock_assignment = MagicMock()
        mock_course.get_assignment.return_value = mock_assignment

        handler._update_backing_assignment(mock_course, '123', {'grading_type': 'percentage'})

        mock_assignment.edit.assert_called_once_with(assignment={'grading_type': 'percentage'})

    def test_omit_from_final_grade(self):
        """omit_from_final_grade is applied to the backing assignment."""
        from unittest.mock import MagicMock
        mock_course = MagicMock()
        mock_assignment = MagicMock()
        mock_course.get_assignment.return_value = mock_assignment

        handler._update_backing_assignment(mock_course, '123', {'omit_from_final_grade': True})

        mock_course.get_assignment.assert_called_once_with(123)
        settings = mock_assignment.edit.call_args[1]['assignment']
        assert settings['grading_type'] == 'points'
        assert settings['omit_from_final_grade'] is True

    def test_hide_in_gradebook_auto_enables_omit(self):
        """hide_in_gradebook: true auto-enables omit_from_final_grade."""
        from unittest.mock import MagicMock
        mock_course = MagicMock()
        mock_assignment = MagicMock()
        mock_course.get_assignment.return_value = mock_assignment

        handler._update_backing_assignment(mock_course, '456', {'hide_in_gradebook': True})

        mock_course.get_assignment.assert_called_once_with(456)
        settings = mock_assignment.edit.call_args[1]['assignment']
        assert settings['grading_type'] == 'points'
        assert settings['omit_from_final_grade'] is True
        assert settings['hide_in_gradebook'] is True

    def test_hide_in_gradebook_false_not_sent(self):
        """hide_in_gradebook: false is NOT sent — Canvas rejects it."""
        from unittest.mock import MagicMock
        mock_course = MagicMock()
        mock_assignment = MagicMock()
        mock_course.get_assignment.return_value = mock_assignment

        handler._update_backing_assignment(mock_course, '456', {'hide_in_gradebook': False})

        settings = mock_assignment.edit.call_args[1]['assignment']
        assert settings['grading_type'] == 'points'
        assert 'hide_in_gradebook' not in settings

    def test_both_settings_combined(self):
        """Both settings sent in a single edit call."""
        from unittest.mock import MagicMock
        mock_course = MagicMock()
        mock_assignment = MagicMock()
        mock_course.get_assignment.return_value = mock_assignment

        handler._update_backing_assignment(mock_course, '789', {
            'omit_from_final_grade': True,
            'hide_in_gradebook': True,
        })

        settings = mock_assignment.edit.call_args[1]['assignment']
        assert settings['grading_type'] == 'points'
        assert settings['omit_from_final_grade'] is True
        assert settings['hide_in_gradebook'] is True

    def test_grading_type_always_triggers_api_call(self):
        """grading_type is always sent, so API call always happens."""
        from unittest.mock import MagicMock
        mock_course = MagicMock()
        mock_assignment = MagicMock()
        mock_course.get_assignment.return_value = mock_assignment

        handler._update_backing_assignment(mock_course, '123', {'points': 10})

        mock_course.get_assignment.assert_called_once_with(123)
        mock_assignment.edit.assert_called_once_with(assignment={'grading_type': 'points'})

    def test_unrelated_meta_ignored(self):
        """Only assignment-level keys are picked up; quiz keys are ignored."""
        from unittest.mock import MagicMock
        mock_course = MagicMock()
        mock_assignment = MagicMock()
        mock_course.get_assignment.return_value = mock_assignment

        handler._update_backing_assignment(mock_course, '123', {
            'shuffle_answers': True,
            'omit_from_final_grade': False,
        })

        settings = mock_assignment.edit.call_args[1]['assignment']
        assert settings['grading_type'] == 'points'
        assert settings['omit_from_final_grade'] is False
        assert 'shuffle_answers' not in settings
