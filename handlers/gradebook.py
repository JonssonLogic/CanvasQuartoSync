"""
Gradebook visibility settings, which always live on a Canvas *assignment*.

Three content types need the same two fields and only the Assignments API
carries them:

  - plain assignments (``type: assignment``),
  - New Quizzes, which are assignment-backed,
  - classic graded quizzes, which own a backing assignment Canvas creates on
    every save of an ``assignment`` / ``graded_survey`` quiz.

Canvas validates ``hide_in_gradebook`` server-side and is strict about it: the
assignment must also be omitted from the final grade *and* be worth 0 points,
or the whole request is rejected with "Hide in gradebook is not included in the
list". Because that rejection takes the entire payload with it - including the
other settings in the same call - the rules are resolved here, offline, before
anything is sent.

Both functions are deliberately pure: they compute fields, they never call the
API and never raise, so a caller can merge the result into a payload it was
going to send anyway without adding a new failure mode.

``hide_in_gradebook`` is *not* offered for classic quizzes. A classic quiz takes
its points from its questions, so hiding one would require every question to be
worth 0 - at which point ``quiz_type: practice_quiz`` is the right tool and
already produces no gradebook column. ``validate_content.py`` says so directly.
"""

from handlers.log import logger


def resolve_gradebook_settings(canvas_meta, points, label=""):
    """Return the gradebook fields to send for this item.

    ``points`` is what the item will actually be worth in Canvas. When it is
    greater than 0 a requested ``hide_in_gradebook`` is dropped with a warning
    rather than sent: Canvas would reject the request, and dropping it costs one
    setting instead of the entire sync of that item.

    An explicit ``omit_from_final_grade`` is always honoured - including
    ``false``, which is how an author turns it back off - except that requesting
    ``hide_in_gradebook`` forces it on, since Canvas requires both together.

    A requested ``hide_in_gradebook: false`` produces nothing on its own - there
    is no point telling Canvas to leave a visible thing visible. Clearing the
    flag on something Canvas *currently* has hidden is :func:`needs_unhide`,
    which the caller merges into the same payload.
    """
    settings = {}

    if 'omit_from_final_grade' in canvas_meta:
        settings['omit_from_final_grade'] = canvas_meta['omit_from_final_grade']

    if canvas_meta.get('hide_in_gradebook'):
        if points:
            where = f" ({label})" if label else ""
            logger.warning(
                "    [yellow]hide_in_gradebook requires points to be 0 or unset%s.[/yellow] "
                "Canvas rejects it when points_possible > 0, so the setting was skipped "
                "and everything else synced.", where)
        else:
            # Canvas requires omit_from_final_grade alongside hide_in_gradebook.
            settings['omit_from_final_grade'] = True
            settings['hide_in_gradebook'] = True

    return settings


def needs_unhide(canvas_meta, current_hidden):
    """True when Canvas has this item hidden but the content no longer asks for it.

    Removing ``hide_in_gradebook`` from a file should put the column back, the
    same way removing a date clears it. The caller merges ``hide_in_gradebook:
    False`` into the payload it was already sending; Canvas accepts the explicit
    ``false`` (verified against a live course - the constraint bites on ``true``
    only), so this costs no extra request.
    """
    return bool(current_hidden) and not canvas_meta.get('hide_in_gradebook')
