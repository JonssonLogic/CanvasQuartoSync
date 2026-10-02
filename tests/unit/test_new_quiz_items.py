"""Tests for NewQuizHandler._transform_question() — per-question item payload.

Focus: per-answer feedback. The qmd parser stores the indented comment under a
checklist answer as ``answer_comments``; New Quizzes takes it as
``entry.answer_feedback`` keyed by choice id, on 'choice' items only.
"""

from handlers.new_quiz_handler import NewQuizHandler


handler = NewQuizHandler()


def _mc(answers, q_type='multiple_choice_question'):
    return {
        'question_name': 'Q',
        'question_text': 'Pick one',
        'question_type': q_type,
        'answers': answers,
    }


class TestAnswerFeedback:
    def test_comment_keyed_by_choice_id(self):
        item = handler._transform_question(_mc([
            {'answer_text': 'A', 'weight': 100, 'answer_comments': 'Right.'},
            {'answer_text': 'B', 'weight': 0, 'answer_comments': 'Wrong, because...'},
        ]), 1)
        entry = item['entry']
        choices = entry['interaction_data']['choices']
        fb = entry['answer_feedback']
        assert fb[choices[0]['id']] == 'Right.'
        assert fb[choices[1]['id']] == 'Wrong, because...'

    def test_answers_without_comment_are_left_out(self):
        item = handler._transform_question(_mc([
            {'answer_text': 'A', 'weight': 100},
            {'answer_text': 'B', 'weight': 0, 'answer_comments': 'No.'},
        ]), 1)
        entry = item['entry']
        choices = entry['interaction_data']['choices']
        assert list(entry['answer_feedback']) == [choices[1]['id']]

    def test_no_comments_means_no_key(self):
        item = handler._transform_question(_mc([
            {'answer_text': 'A', 'weight': 100},
            {'answer_text': 'B', 'weight': 0},
        ]), 1)
        assert 'answer_feedback' not in item['entry']

    def test_rendered_html_comment_is_passed_through(self):
        item = handler._transform_question(_mc([
            {'answer_html': '<p>A</p>', 'weight': 100,
             'answer_comments': '<p>Stress is <em>F/A</em>.</p>'},
            {'answer_html': '<p>B</p>', 'weight': 0},
        ]), 1)
        fb = item['entry']['answer_feedback']
        assert list(fb.values()) == ['<p>Stress is <em>F/A</em>.</p>']

    def test_multi_answer_does_not_send_answer_feedback(self):
        item = handler._transform_question(_mc([
            {'answer_text': 'A', 'weight': 100, 'answer_comments': 'x'},
            {'answer_text': 'B', 'weight': 100, 'answer_comments': 'y'},
        ], q_type='multiple_answers_question'), 1)
        assert 'answer_feedback' not in item['entry']

    def test_whole_question_feedback_still_sent(self):
        q = _mc([
            {'answer_text': 'A', 'weight': 100, 'answer_comments': 'x'},
            {'answer_text': 'B', 'weight': 0},
        ])
        q['correct_comments'] = 'Well done'
        q['incorrect_comments'] = 'Try again'
        item = handler._transform_question(q, 1)
        assert item['entry']['feedback'] == {'correct': 'Well done', 'incorrect': 'Try again'}
        assert len(item['entry']['answer_feedback']) == 1


class TestShuffleChoices:
    """Quiz-level shuffle_answers doesn't tick the per-item "Shuffle Choices"
    box in New Quizzes; it lives in entry.properties.shuffle_rules."""

    def _rules(self, item):
        return item['entry']['properties']['shuffle_rules']['choices']

    def test_shuffled_when_asked(self):
        item = handler._transform_question(_mc([
            {'answer_text': 'A', 'weight': 100},
            {'answer_text': 'B', 'weight': 0},
        ]), 1, shuffle_choices=True)
        assert self._rules(item) == {'shuffled': True, 'to_lock': []}

    def test_not_shuffled_by_default(self):
        item = handler._transform_question(_mc([
            {'answer_text': 'A', 'weight': 100},
        ]), 1)
        assert self._rules(item)['shuffled'] is False

    def test_multi_answer_shuffled_too(self):
        item = handler._transform_question(_mc([
            {'answer_text': 'A', 'weight': 100},
            {'answer_text': 'B', 'weight': 100},
        ], q_type='multiple_answers_question'), 1, shuffle_choices=True)
        assert self._rules(item)['shuffled'] is True

    def test_true_false_has_no_shuffle_rules(self):
        item = handler._transform_question({
            'question_name': 'TF', 'question_text': 'x',
            'question_type': 'true_false_question',
            'answers': [{'answer_text': 'True', 'weight': 100}],
        }, 1, shuffle_choices=True)
        assert 'shuffle_rules' not in item['entry']['properties']

    def test_properties_live_on_entry_not_item(self):
        item = handler._transform_question(_mc([{'answer_text': 'A', 'weight': 100}]), 1)
        assert 'properties' not in item
        assert 'properties' in item['entry']

    def test_sync_questions_passes_flag_from_handler(self, monkeypatch):
        seen = []
        orig = handler._transform_question

        def spy(q, pos, shuffle_choices=False):
            seen.append(shuffle_choices)
            return orig(q, pos, shuffle_choices=shuffle_choices)

        monkeypatch.setattr(handler, '_transform_question', spy)

        class Client:
            def list_items(self, *a): return []
            def create_item(self, *a, **k): return {'id': '1'}
            def update_item(self, *a, **k): return {'id': '1'}
            def delete_item(self, *a, **k): pass

        handler._sync_questions(Client(), 1, 2, [_mc([{'answer_text': 'A', 'weight': 100}])],
                                None, 'x.qmd', 0, None, shuffle_choices=True)
        assert seen == [True]


class TestRenderQueuesAnswerComments:
    """_render_qmd_questions must send answer comments through Quarto too.

    Quarto isn't run here: the render step is exercised only up to the chunk
    list by monkeypatching subprocess to fail, which makes the handler fall
    back to the processed markdown — enough to see the comment survive.
    """

    def test_comment_survives_render_fallback(self, monkeypatch, tmp_path):
        import handlers.new_quiz_handler as m

        def boom(*a, **k):
            raise RuntimeError('no quarto in tests')

        monkeypatch.setattr(m.subprocess, 'run', boom)
        monkeypatch.setattr(m, 'process_content', lambda text, *a, **k: text)
        qs = [_mc([
            {'answer_text': 'A', 'weight': 100, 'answer_comments': 'Right because $F/A$.'},
            {'answer_text': 'B', 'weight': 0},
        ])]
        out = handler._render_qmd_questions(qs, str(tmp_path), course=None, content_root=str(tmp_path))
        assert out[0]['answers'][0]['answer_comments'] == 'Right because $F/A$.'
        assert 'answer_comments' not in out[0]['answers'][1]
