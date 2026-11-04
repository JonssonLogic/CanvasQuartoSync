"""A quiz students have answered: Canvas locks its questions.

The update came back 422 "cannot update an immutable item", the handler then
created a new copy of every question and left the old one in place, so the
published quiz had each question twice (Quiz: Kuggar, 2026-11-04).
"""

import json

from handlers.new_quiz_handler import NewQuizHandler


def _q(name):
    return {'question_name': name, 'question_text': 'Pick one',
            'question_type': 'multiple_choice_question',
            'answers': [{'answer_text': 'A', 'weight': 100}, {'answer_text': 'B', 'weight': 0}]}


class LockedClient:
    def __init__(self, items, error="422 Client Error: Unprocessable Entity - "
                 '{"errors":{"base":["cannot update an immutable item"]}}'):
        self.items = items
        self.error = error
        self.created, self.deleted = [], []

    def list_items(self, *a):
        return [{'id': i, 'entry': {'title': t}} for t, i in self.items.items()]

    def update_item(self, *a, **k):
        raise Exception(self.error)

    def create_item(self, *a, **k):
        self.created.append(a)
        return {'id': str(1000 + len(self.created))}

    def delete_item(self, course_id, quiz_id, item_id):
        self.deleted.append(item_id)


def _sync(tmp_path, client, names):
    (tmp_path / ".canvas_sync_map.json").write_text("{}", encoding="utf-8")
    entry = {'item_ids': dict(client.items)}
    NewQuizHandler()._sync_questions(client, 1, 2, [_q(n) for n in names],
                                     str(tmp_path), str(tmp_path / "quiz.qmd"), 'h', entry)
    return json.loads((tmp_path / ".canvas_sync_map.json").read_text(encoding="utf-8"))


def test_locked_questions_are_kept_not_copied(tmp_path, caplog):
    client = LockedClient({'Q1': '42625', 'Q2': '42626'})
    saved = _sync(tmp_path, client, ['Q1', 'Q2'])

    assert client.created == []
    assert client.deleted == []
    assert saved['quiz.qmd']['item_ids'] == {'Q1': '42625', 'Q2': '42626'}
    assert "2 question(s) not updated" in caplog.text


def test_other_update_errors_keep_the_question_too(tmp_path):
    client = LockedClient({'Q1': '42625'}, error="500 Server Error")
    saved = _sync(tmp_path, client, ['Q1'])

    assert client.created == []
    assert saved['quiz.qmd']['item_ids'] == {'Q1': '42625'}
