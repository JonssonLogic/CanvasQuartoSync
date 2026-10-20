"""
What a sync does when Canvas has been edited since the last publish.

Every synced item answers two questions, each against a record in the shared
sync map:

1. Has the source changed since it was published?  (``source_hash``)
2. Has Canvas changed since it was published?      (``canvas_hash``, and
   ``canvas_settings_hash`` for assignments)

=========  =========  ==========================================================
Source     Canvas     Outcome
=========  =========  ==========================================================
same       same       skip
changed    same       publish
same       changed    someone edited Canvas: keep it, save a copy, report
changed    changed    conflict: team refuses, solo overwrites; both save a copy
=========  =========  ==========================================================

Before this, a Canvas edit was only noticed when the source had also changed,
and was then overwritten after a warning. In a course several people edit,
that silently throws away a colleague's fix, so ``workflow = "team"`` refuses
instead. Solo keeps overwriting, because there the person syncing is usually
the person who made the Canvas edit, but the Canvas version is now always
saved to ``.canvas_drift/`` first, so nothing is lost either way.

``--accept-drift FILE`` (or ``all``) is the way out of both drift rows: it
publishes the repo version over the Canvas edit. Use it after bringing the
Canvas change into the .qmd, or to discard the Canvas change.
"""

import json
import os
from dataclasses import dataclass, field

from handlers.log import logger

SOLO = 'solo'
TEAM = 'team'
DRIFT_DIR = '.canvas_drift'


@dataclass
class DriftEvent:
    file: str
    title: str
    outcome: str            # 'kept' | 'refused' | 'overwritten' | 'accepted'
    saved_to: str = ''
    edited_by: str = ''
    edited_at: str = ''
    what: str = 'content'   # 'content' | 'settings' | 'content and settings'


@dataclass
class SyncPolicy:
    workflow: str = SOLO
    accept: set = field(default_factory=set)
    events: list = field(default_factory=list)

    def accepts(self, rel_path):
        return 'all' in self.accept or rel_path in self.accept

    def review(self, content_root, file_path, title, canvas_obj, canvas_html,
               needs_render, body_drifted, settings_drifted=False, kind=None):
        """Decide what happens to an item whose Canvas copy has been checked.

        Returns True if the item should be published, False if not. With no
        drift, that is just ``needs_render``.
        """
        if not (body_drifted or settings_drifted):
            return needs_render

        rel = os.path.relpath(file_path, content_root).replace('\\', '/')
        what = ('content and settings' if body_drifted and settings_drifted
                else 'settings' if settings_drifted else 'content')
        who, when = edit_info(canvas_obj)

        if self.accepts(rel):
            self.events.append(DriftEvent(rel, title, 'accepted',
                                          edited_by=who, edited_at=when, what=what))
            logger.warning("    [yellow]Canvas was edited (%s), overwriting as asked "
                           "with --accept-drift.[/yellow]", what)
            return True

        saved = preserve_canvas_version(content_root, rel, canvas_obj, canvas_html,
                                        who, when, what, kind)

        if not needs_render:
            outcome, publish = 'kept', False
            logger.warning("    [yellow]Edited in Canvas[/yellow] (%s%s). The source has "
                           "not changed, so the Canvas version stays. Saved to %s",
                           what, _by(who, when), saved)
        elif self.workflow == TEAM:
            outcome, publish = 'refused', False
            logger.error("    [red]Not published: edited in Canvas[/red] (%s%s) and changed "
                         "in the repo. Canvas version saved to %s", what, _by(who, when), saved)
        else:
            outcome, publish = 'overwritten', True
            logger.warning("    [yellow]DRIFT DETECTED:[/yellow] '%s' was edited in Canvas "
                           "(%s%s). Overwriting with the local version; the Canvas version "
                           "is saved to %s", title, what, _by(who, when), saved)

        self.events.append(DriftEvent(rel, title, outcome, saved, who, when, what))
        return publish

    def report(self):
        """Summarise drift at the end of a sync.

        Returns True if a conflict was refused, which needs the user's action
        and is reported as exit code 2. Kept Canvas edits are reported but are
        not a failure: the sync did what it should.
        """
        if not self.events:
            return False
        held = [e for e in self.events if e.outcome in ('kept', 'refused')]
        logger.warning("[bold yellow]Edited in Canvas since the last sync:[/bold yellow]")
        labels = {
            'kept': 'kept the Canvas version',
            'refused': 'NOT PUBLISHED, conflict',
            'overwritten': 'overwritten, copy saved',
            'accepted': 'overwritten (--accept-drift)',
        }
        for e in self.events:
            logger.warning("  %-30s %s  [dim]%s%s[/dim]", labels[e.outcome], e.file,
                           e.what, _by(e.edited_by, e.edited_at))
        if held:
            logger.warning(
                "  To resolve: bring the Canvas change into the .qmd (the saved copy "
                "is in %s/), commit, and run the sync with --accept-drift <file>. "
                "To throw the Canvas change away, run --accept-drift <file> directly.",
                DRIFT_DIR)
        return any(e.outcome == 'refused' for e in self.events)


def _by(who, when):
    parts = []
    if who:
        parts.append(f"by {who}")
    if when:
        parts.append(when)
    return (", " + " ".join(parts)) if parts else ""


def edit_info(canvas_obj):
    """Who last edited this item in Canvas, and when, as far as Canvas says.

    Pages keep revisions with the editor's name. Assignments and quizzes only
    carry ``updated_at``.
    """
    who, when = '', ''
    if hasattr(canvas_obj, 'page_id'):
        try:
            rev = canvas_obj.show_latest_revision()
            editor = getattr(rev, 'edited_by', None)
            if isinstance(editor, dict):
                who = editor.get('display_name') or ''
            when = getattr(rev, 'updated_at', '')
        except Exception:
            pass
    if not isinstance(when, str) or not when:
        when = getattr(canvas_obj, 'updated_at', '')
    return (who if isinstance(who, str) else '',
            when if isinstance(when, str) else '')


def preserve_canvas_version(content_root, rel_path, canvas_obj, canvas_html,
                            who='', when='', what='content', kind=None):
    """Save Canvas's current version before anything can overwrite it.

    Writes the raw HTML, a .qmd conversion for a side-by-side diff where one
    can be made, and a small JSON note saying who edited it and when.
    Returns the directory-relative path of the HTML copy.
    """
    d = os.path.join(content_root, DRIFT_DIR)
    os.makedirs(d, exist_ok=True)
    stem = rel_path.replace('/', '__').replace(' ', '_')
    is_quiz = kind in ('classic_quiz', 'new_quiz')
    # A quiz is saved as the canonical JSON its fingerprint is built from.
    html_path = os.path.join(d, stem + ('.canvas.json' if is_quiz else '.canvas.html'))
    try:
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(canvas_html or '')
    except OSError as e:
        logger.debug("    Could not save Canvas version: %s", e)

    if not is_quiz:
        try:
            from handlers.drift_detector import _canvas_html_to_qmd
            from handlers.content_utils import load_sync_map
            item_type = kind or ('page' if hasattr(canvas_obj, 'page_id') else 'assignment')
            qmd = _canvas_html_to_qmd(canvas_html or '', item_type, canvas_obj,
                                      load_sync_map(content_root), content_root)
            with open(os.path.join(d, stem + '.canvas.qmd'), 'w', encoding='utf-8') as f:
                f.write(qmd)
        except Exception as e:
            logger.debug("    Could not convert Canvas version to .qmd: %s", e)

    try:
        with open(os.path.join(d, stem + '.json'), 'w', encoding='utf-8') as f:
            json.dump({'file': rel_path, 'edited_by': who, 'edited_at': when,
                       'what': what}, f, indent=2, ensure_ascii=False)
    except OSError:
        pass

    return os.path.relpath(html_path, content_root).replace('\\', '/')


# The policy for the current run. sync_to_canvas sets it from config.toml and
# the command line; handlers read it. Defaults to solo with nothing accepted,
# which is what tests and library callers get.
_current = SyncPolicy()


def get_policy():
    return _current


def set_policy(policy):
    global _current
    _current = policy
    return policy
