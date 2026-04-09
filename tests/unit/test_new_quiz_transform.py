"""Tests for NewQuizHandler._transform_question() — question type mapping."""

import uuid
from unittest.mock import patch
from handlers.new_quiz_handler import NewQuizHandler


handler = NewQuizHandler()


class TestMultipleChoice:

    def test_basic_transform(self):
        q = {
            "question_name": "Q1",
            "question_type": "multiple_choice_question",
            "question_text": "Pick one",
            "points_possible": 2,
            "answers": [
                {"answer_text": "A", "answer_weight": 100},
                {"answer_text": "B", "answer_weight": 0},
            ],
        }
        result = handler._transform_question(q, 1)
        assert result["entry"]["interaction_type_slug"] == "choice"
        assert result["entry"]["scoring_algorithm"] == "Equivalence"
        assert result["points_possible"] == 2.0
        choices = result["entry"]["interaction_data"]["choices"]
        assert len(choices) == 2
        # The correct answer's ID should be in scoring_data
        correct_id = result["entry"]["scoring_data"]["value"]
        assert correct_id == choices[0]["id"]

    def test_position_set(self):
        q = {
            "question_name": "Q5",
            "question_type": "multiple_choice_question",
            "answers": [{"answer_text": "A", "answer_weight": 100}],
        }
        result = handler._transform_question(q, 5)
        assert result["position"] == 5

    def test_default_points(self):
        q = {
            "question_name": "Q1",
            "question_type": "multiple_choice_question",
            "answers": [{"answer_text": "A", "answer_weight": 100}],
        }
        result = handler._transform_question(q, 1)
        assert result["points_possible"] == 1.0


class TestTrueFalse:

    def test_true_correct(self):
        q = {
            "question_name": "TF1",
            "question_type": "true_false_question",
            "question_text": "Is sky blue?",
            "answers": [
                {"answer_text": "True", "answer_weight": 100},
                {"answer_text": "False", "answer_weight": 0},
            ],
        }
        result = handler._transform_question(q, 1)
        assert result["entry"]["interaction_type_slug"] == "true-false"
        assert result["entry"]["scoring_algorithm"] == "Equivalence"
        assert result["entry"]["scoring_data"]["value"] is True

    def test_false_correct(self):
        q = {
            "question_name": "TF2",
            "question_type": "true_false_question",
            "answers": [
                {"answer_text": "True", "answer_weight": 0},
                {"answer_text": "False", "answer_weight": 100},
            ],
        }
        result = handler._transform_question(q, 1)
        assert result["entry"]["scoring_data"]["value"] is False


class TestMultiAnswer:

    def test_multiple_correct(self):
        q = {
            "question_name": "MA1",
            "question_type": "multiple_answers_question",
            "answers": [
                {"answer_text": "A", "answer_weight": 100},
                {"answer_text": "B", "answer_weight": 100},
                {"answer_text": "C", "answer_weight": 0},
            ],
        }
        result = handler._transform_question(q, 1)
        assert result["entry"]["interaction_type_slug"] == "multi-answer"
        assert result["entry"]["scoring_algorithm"] == "AllOrNothing"
        correct_ids = result["entry"]["scoring_data"]["value"]
        assert len(correct_ids) == 2


class TestNumeric:

    def test_margin_of_error(self):
        q = {
            "question_name": "N1",
            "question_type": "numeric_question",
            "answers": [
                {"value": "200", "margin": "5", "margin_type": "percent", "answer_weight": 100},
            ],
        }
        result = handler._transform_question(q, 1)
        assert result["entry"]["interaction_type_slug"] == "numeric"
        assert result["entry"]["scoring_algorithm"] == "Numeric"
        scoring_values = result["entry"]["scoring_data"]["value"]
        assert len(scoring_values) == 1
        assert scoring_values[0]["type"] == "marginOfError"
        assert scoring_values[0]["value"] == "200"
        assert scoring_values[0]["margin"] == "5"

    def test_range_answer(self):
        q = {
            "question_name": "N2",
            "question_type": "numeric_question",
            "answers": [
                {"start": "10", "end": "20", "answer_weight": 100},
            ],
        }
        result = handler._transform_question(q, 1)
        scoring_values = result["entry"]["scoring_data"]["value"]
        assert scoring_values[0]["type"] == "withinARange"
        assert scoring_values[0]["start"] == "10"
        assert scoring_values[0]["end"] == "20"


class TestFormula:

    def test_formula_structure(self):
        q = {
            "question_name": "F1",
            "question_type": "formula_question",
            "question_text": "Calculate [A]*[B]",
            "formula": "A*B",
            "margin": "2",
            "margin_type": "percent",
            "answer_count": 3,
            "variables": [
                {"name": "A", "min": 1, "max": 10, "precision": 0},
                {"name": "B", "min": 1, "max": 10, "precision": 0},
            ],
        }
        result = handler._transform_question(q, 1)
        assert result["entry"]["interaction_type_slug"] == "formula"
        assert result["entry"]["scoring_algorithm"] == "Numeric"
        scoring = result["entry"]["scoring_data"]["value"]
        assert scoring["formula"] == "A*B"
        assert len(scoring["generated_solutions"]) == 3
        assert len(scoring["variables"]) == 2


class TestFeedback:

    def test_correct_incorrect_feedback(self):
        q = {
            "question_name": "Q1",
            "question_type": "multiple_choice_question",
            "correct_comments": "Well done!",
            "incorrect_comments": "Try again.",
            "answers": [{"answer_text": "A", "answer_weight": 100}],
        }
        result = handler._transform_question(q, 1)
        assert result["entry"]["feedback"]["correct"] == "Well done!"
        assert result["entry"]["feedback"]["incorrect"] == "Try again."
