import os
import json
import uuid
import subprocess
import re

import frontmatter
from handlers.base_handler import BaseHandler
from handlers.content_utils import get_mapped_id, save_mapped_id, parse_module_name, load_sync_map, save_sync_map, process_content
from handlers.qmd_quiz_parser import parse_qmd_quiz
from handlers.new_quiz_api import NewQuizAPIClient, NewQuizAPIError
from handlers.log import logger

class NewQuizHandler(BaseHandler):
    """
    Handler for Canvas New Quizzes (assignment-backed).
    Expects QMD with `canvas.type: new_quiz` or JSON with `canvas.quiz_engine: new`.
    """
    def can_handle(self, file_path: str) -> bool:
        if file_path.endswith('.qmd'):
            try:
                post = frontmatter.load(file_path)
                return post.metadata.get('canvas', {}).get('type') == 'new_quiz'
            except:
                pass
        elif file_path.endswith('.json'):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return data.get('canvas', {}).get('quiz_engine') == 'new'
            except:
                pass
        return False

    def sync(self, file_path: str, course, module=None, canvas_obj=None, content_root=None):
        filename = os.path.basename(file_path)
        logger.info("  [cyan]Syncing new quiz:[/cyan] [bold]%s[/bold]", filename)

        # Instantiate API Client
        api_url = os.environ.get("CANVAS_API_URL")
        api_token = os.environ.get("CANVAS_API_TOKEN")
        client = NewQuizAPIClient(api_url, api_token)
        course_id = course.id

        is_qmd = file_path.endswith('.qmd')
        questions_data = []
        canvas_meta = {}

        if is_qmd:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    raw_content = f.read()
                canvas_meta, questions_data = parse_qmd_quiz(raw_content)
            except Exception as e:
                logger.error("    Failed to load QMD new quiz: %s", e)
                return
        else:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                questions_data = data.get('questions', [])
                canvas_meta = data.get('canvas', {})
            except Exception as e:
                logger.error("    Failed to load JSON new quiz: %s", e)
                return

        title_override = canvas_meta.get('title')
        title = title_override if title_override else parse_module_name(os.path.splitext(filename)[0])
        indent = canvas_meta.get('indent', 0)
        published = canvas_meta.get('published', False)

        current_mtime = os.path.getmtime(file_path)
        existing_id, map_entry = get_mapped_id(content_root, file_path) if content_root else (None, None)

        needs_update = True
        quiz_obj = None

        # 1c. Process Content (ALWAYS, to track ACTIVE_ASSET_IDS for pruning)
        base_path = os.path.dirname(file_path)
        _ = process_content(raw_content if is_qmd else json.dumps(data), base_path, course, content_root=content_root)

        # Check sync map for existing quiz
        if existing_id and isinstance(map_entry, dict):
            if map_entry.get('mtime') == current_mtime:
                logger.debug("    No changes detected, skipping update")
                needs_update = False

            # Always try to fetch the existing quiz when we have an ID,
            # regardless of whether the file changed or not.
            try:
                quiz_obj = client.get_quiz(course_id, existing_id)
            except Exception as e:
                logger.warning("    Previously synced new quiz not found in Canvas, re-creating")
                quiz_obj = None
                needs_update = True

        # Build quiz payload with the user's published state directly.
        quiz_payload = self._build_quiz_payload(title, published, canvas_meta)

        if needs_update:
            # Render question content through Quarto (LaTeX, markdown, images)
            base_path = os.path.dirname(file_path)
            questions_data = self._render_qmd_questions(questions_data, base_path, course, content_root)

            try:
                if quiz_obj:
                    # Repair the backing assignment BEFORE updating the quiz.
                    # Canvas validates assignment-level constraints (e.g.
                    # hide_in_gradebook) during quiz updates — a prior failed
                    # sync can leave the assignment in an invalid state that
                    # blocks all subsequent quiz API calls.
                    self._update_backing_assignment(course, existing_id, canvas_meta)

                    logger.info("    [yellow]Updating new quiz:[/yellow] %s", title)
                    quiz_obj = client.update_quiz(course_id, existing_id, quiz_payload)
                else:
                    # Fallback title search to adopt stubs
                    assignments = course.get_assignments(search_term=title)
                    stub_assignment = None
                    for a in assignments:
                        if a.name == title:
                            stub_assignment = a
                            break

                    if stub_assignment:
                        existing_id = str(stub_assignment.id)
                        logger.info("    [yellow]Adopting existing stub:[/yellow] %s", title)
                        self._update_backing_assignment(course, existing_id, canvas_meta)
                        quiz_obj = client.update_quiz(course_id, existing_id, quiz_payload)
                    else:
                        logger.info("    [green]Creating new quiz:[/green] %s", title)
                        quiz_obj = client.create_quiz(course_id, quiz_payload)
                        existing_id = str(quiz_obj['id'])
                        self._update_backing_assignment(course, existing_id, canvas_meta)

                    map_entry = None  # Clear stale item IDs — new quiz has no items yet

                # Sync questions
                self._sync_questions(client, course_id, existing_id, questions_data, content_root, file_path, current_mtime, map_entry)

            except NewQuizAPIError as e:
                logger.exception("    New Quiz API error: %s", e)
                return

        # Add to Module
        if module and existing_id:
            return self.add_to_module(module, {
                'type': 'Assignment',
                'content_id': existing_id,
                'title': title,
                'published': published
            }, indent=indent)

    def _update_backing_assignment(self, course, assignment_id, canvas_meta):
        """Apply assignment-level settings to the New Quiz's backing assignment.

        New Quizzes are assignment-backed, so settings like omit_from_final_grade
        and hide_in_gradebook must be set via the Assignments API, not the quiz API.

        Canvas enforces constraints on hide_in_gradebook:
        - It requires omit_from_final_grade to also be true.
        - It requires points_possible to be 0 or unset.
        When hide_in_gradebook is requested, we auto-enable omit_from_final_grade
        and warn if points are set (since Canvas will reject the request).
        """
        assignment_settings = {}
        # grading_type must be set on the backing assignment for autograding
        # to work and for results to be visible. Defaults to "points" which
        # matches what Canvas UI sets when creating a graded New Quiz.
        grading_type = canvas_meta.get('grading_type', 'points')
        assignment_settings['grading_type'] = grading_type
        if 'omit_from_final_grade' in canvas_meta:
            assignment_settings['omit_from_final_grade'] = canvas_meta['omit_from_final_grade']
        if canvas_meta.get('hide_in_gradebook'):
            # Canvas requires omit_from_final_grade when hide_in_gradebook is true
            assignment_settings['omit_from_final_grade'] = True
            assignment_settings['hide_in_gradebook'] = True
            if canvas_meta.get('points'):
                logger.warning("    [yellow]hide_in_gradebook requires points to be 0 or unset.[/yellow] "
                               "Canvas will reject the request when points_possible > 0.")
        # Note: do NOT send hide_in_gradebook: false explicitly — Canvas
        # rejects it with "is not included in the list". Omitting the field
        # keeps it at its current (default false) state.
        if assignment_settings:
            try:
                assignment = course.get_assignment(int(assignment_id))
                assignment.edit(assignment=assignment_settings)
                logger.debug("    Updated backing assignment settings: %s", list(assignment_settings.keys()))
            except Exception as e:
                logger.warning("    Failed to update backing assignment settings: %s", e)

    def _build_quiz_payload(self, title, published, canvas_meta):
        """Build the quiz-level settings payload for the New Quizzes API.

        The New Quizzes API nests display/behavior settings inside a
        ``quiz_settings`` object, with multiple-attempt fields nested one
        level deeper in ``quiz_settings.multiple_attempts``.  Top-level
        fields are limited to title, published, points, dates, and
        instructions.
        """
        quiz_payload = {
            'title': title,
            'published': published,
        }

        # --- Top-level fields ---
        if 'points' in canvas_meta:
            quiz_payload['points_possible'] = canvas_meta['points']
        if 'due_at' in canvas_meta:
            quiz_payload['due_at'] = canvas_meta['due_at'] or ''
        if 'unlock_at' in canvas_meta:
            quiz_payload['unlock_at'] = canvas_meta['unlock_at'] or ''
        if 'lock_at' in canvas_meta:
            quiz_payload['lock_at'] = canvas_meta['lock_at'] or ''
        if 'instructions' in canvas_meta:
            quiz_payload['instructions'] = canvas_meta['instructions']
        # --- quiz_settings (nested) ---
        quiz_settings = {}

        if 'shuffle_answers' in canvas_meta:
            quiz_settings['shuffle_answers'] = canvas_meta['shuffle_answers']
        if 'shuffle_questions' in canvas_meta:
            quiz_settings['shuffle_questions'] = canvas_meta['shuffle_questions']

        # Time limit — value is in seconds for New Quizzes
        if 'time_limit' in canvas_meta:
            quiz_settings['has_time_limit'] = True
            quiz_settings['session_time_limit_in_seconds'] = canvas_meta['time_limit']

        # One-at-a-time and backtracking (Classic parity: same YAML keys)
        if 'one_question_at_a_time' in canvas_meta:
            quiz_settings['one_at_a_time_type'] = 'question' if canvas_meta['one_question_at_a_time'] else 'none'
        if 'cant_go_back' in canvas_meta:
            quiz_settings['allow_backtracking'] = not canvas_meta['cant_go_back']

        # Access code (Classic parity: same YAML key)
        if 'access_code' in canvas_meta:
            quiz_settings['require_student_access_code'] = True
            quiz_settings['student_access_code'] = canvas_meta['access_code']

        # Calculator type
        if 'calculator_type' in canvas_meta:
            quiz_settings['calculator_type'] = canvas_meta['calculator_type']

        # --- quiz_settings.multiple_attempts (nested) ---
        multiple_attempts = {}

        if 'allowed_attempts' in canvas_meta:
            attempts = canvas_meta['allowed_attempts']
            multiple_attempts['multiple_attempts_enabled'] = attempts != 1
            if attempts > 1:
                multiple_attempts['max_attempts'] = attempts
            # Canvas requires score_to_keep when multiple attempts are enabled.
            # Valid values (matching what Canvas GET returns): highest, latest,
            # average, first.
            if attempts != 1:
                multiple_attempts['score_to_keep'] = canvas_meta.get(
                    'score_to_keep', 'highest')

        if 'cooling_period_seconds' in canvas_meta:
            multiple_attempts['cooling_period_seconds'] = canvas_meta['cooling_period_seconds']

        if multiple_attempts:
            quiz_settings['multiple_attempts'] = multiple_attempts

        # --- quiz_settings.result_view_settings (nested) ---
        result_view_meta = canvas_meta.get('result_view', {})
        if isinstance(result_view_meta, dict) and result_view_meta:
            _RV_MAP = {
                'restricted':               'result_view_restricted',
                'show_questions':           'display_items',
                'show_student_responses':   'display_item_response',
                'show_responses_frequency': 'display_item_response_qualifier',
                'show_responses_at':        'show_item_responses_at',
                'hide_responses_at':        'hide_item_responses_at',
                'show_correctness':         'display_item_response_correctness',
                'show_correctness_at':      'show_item_correctness_at',
                'hide_correctness_at':      'hide_item_correctness_at',
                'show_correct_answers':     'display_item_correct_answer',
                'show_feedback':            'display_item_feedback',
                'show_points_awarded':      'display_points_awarded',
                'show_points_possible':     'display_points_possible',
            }
            result_view = {}
            for yaml_key, api_key in _RV_MAP.items():
                if yaml_key in result_view_meta:
                    result_view[api_key] = result_view_meta[yaml_key]
            if result_view:
                quiz_settings['result_view_settings'] = result_view

        if quiz_settings:
            quiz_payload['quiz_settings'] = quiz_settings

        return quiz_payload

    def _render_qmd_questions(self, questions_data, base_path, course, content_root):
        """
        Render markdown/LaTeX content in quiz questions to HTML.
        Batches all content into a single Quarto render for performance.
        Uses <div id="qchunk-N"> markers to split the output back into pieces.
        """
        # Step 1: Collect all markdown pieces that need rendering
        chunks = []  # list of (key, markdown_text)

        for qi, q in enumerate(questions_data):
            is_formula = q.get('question_type') == 'formula_question'
            formula_vars = []
            if is_formula:
                variables_list = q.get('variables', [])
                for v in variables_list:
                    if 'name' in v and v['name']:
                        formula_vars.append(re.escape(v['name']))

            var_pattern = None
            if formula_vars:
                # Match [var] where var is exactly one of the defined variable names
                var_regex = r'\[(' + '|'.join(formula_vars) + r')\]'
                var_pattern = re.compile(var_regex)

            if q.get('question_text'):
                text = q['question_text']
                if var_pattern:
                    text = var_pattern.sub(r'QVAR_START_\1_QVAR_END', text)
                chunks.append((f"q{qi}_text", text))

            for ai, ans in enumerate(q.get('answers', [])):
                if ans.get('answer_html'):
                    text = ans['answer_html']
                    if var_pattern:
                        text = var_pattern.sub(r'QVAR_START_\1_QVAR_END', text)
                    chunks.append((f"q{qi}_a{ai}", text))
                elif ans.get('answer_text'):
                    text = ans['answer_text']
                    if var_pattern:
                        text = var_pattern.sub(r'QVAR_START_\1_QVAR_END', text)
                    chunks.append((f"q{qi}_a{ai}", text))

            for comment_key in ['correct_comments', 'incorrect_comments']:
                if q.get(comment_key):
                    text = q[comment_key]
                    if var_pattern:
                        text = var_pattern.sub(r'QVAR_START_\1_QVAR_END', text)
                    chunks.append((f"q{qi}_{comment_key}", text))

        if not chunks:
            return questions_data

        logger.debug("    Rendering %d questions through Quarto...", len(questions_data))

        # Step 2: Process images/links in all chunks
        processed_chunks = {}
        for key, md_text in chunks:
            processed_chunks[key] = process_content(
                md_text, base_path, course, content_root=content_root
            )

        # Step 3: Combine into a single QMD document with div markers
        qmd_parts = ["---\ntitle: \"\"\n---\n"]
        chunk_keys = list(processed_chunks.keys())

        for key in chunk_keys:
            qmd_parts.append(f'\n\n::: {{#qchunk-{key}}}\n{processed_chunks[key]}\n:::\n')

        qmd_content = ''.join(qmd_parts)

        # Step 4: Single Quarto render
        temp_qmd = os.path.join(base_path, "_temp_quiz_render.qmd")
        temp_html = os.path.join(base_path, "_temp_quiz_render.html")
        temp_files_dir = os.path.join(base_path, "_temp_quiz_render_files")

        rendered_map = {}

        try:
            with open(temp_qmd, 'w', encoding='utf-8') as f:
                f.write(qmd_content)

            cmd = ["quarto", "render", temp_qmd, "--to", "html"]
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            if os.path.exists(temp_html):
                with open(temp_html, 'r', encoding='utf-8') as f:
                    full_html = f.read()

                # Extract main content
                main_match = re.search(
                    r'<main[^>]*id="quarto-document-content"[^>]*>(.*?)</main>',
                    full_html, re.DOTALL
                )
                html_body = main_match.group(1) if main_match else full_html
                html_body = re.sub(
                    r'<header[^>]*id="title-block-header"[^>]*>.*?</header>',
                    '', html_body, flags=re.DOTALL
                )

                # Step 5: Split by div markers
                for key in chunk_keys:
                    pattern = rf'<div\s+id="qchunk-{re.escape(key)}"[^>]*>\s*(.*?)\s*</div>'
                    match = re.search(pattern, html_body, re.DOTALL)
                    if match:
                        rendered_map[key] = match.group(1).strip()
                    else:
                        rendered_map[key] = processed_chunks[key]

                    # Unescape formula variables and convert to New Quizzes syntax
                    rendered_map[key] = re.sub(r'QVAR_START_([a-zA-Z0-9_-]+)_QVAR_END', r'`\1`', rendered_map[key])
            else:
                logger.warning("    Quarto render produced no output, using processed markdown")
                rendered_map = processed_chunks

        except Exception as e:
            logger.warning("    Quarto render error: %s", e)
            rendered_map = processed_chunks
        finally:
            self._cleanup(temp_qmd, temp_html, temp_files_dir)

        # Step 6: Apply rendered HTML back to question data
        rendered_questions = []
        for qi, q in enumerate(questions_data):
            q = dict(q)

            text_key = f"q{qi}_text"
            if text_key in rendered_map:
                q['question_text'] = rendered_map[text_key]

            if q.get('answers'):
                rendered_answers = []
                for ai, ans in enumerate(q['answers']):
                    ans = dict(ans)
                    ans_key = f"q{qi}_a{ai}"
                    if ans_key in rendered_map:
                        ans['answer_html'] = rendered_map[ans_key]
                        ans.pop('answer_text', None)
                    rendered_answers.append(ans)
                q['answers'] = rendered_answers

            for comment_key in ['correct_comments', 'incorrect_comments']:
                ck = f"q{qi}_{comment_key}"
                if ck in rendered_map:
                    q[comment_key] = rendered_map[ck]

            rendered_questions.append(q)

        return rendered_questions

    def _sync_questions(self, client, course_id, assignment_id, questions_data, content_root, file_path, mtime, map_entry):
        logger.info("    [cyan]Syncing %d questions to new quiz...[/cyan]", len(questions_data))

        # Load existing items from Canvas
        existing_items_resp = client.list_items(course_id, assignment_id)
        existing_items = existing_items_resp if isinstance(existing_items_resp, list) else []

        # Map existing items by their entry's title/question_name (if available)
        # New Quizzes API returns items with an 'entry' dict that contains the 'title'
        existing_q_map = {}
        for item in existing_items:
            entry = item.get('entry', {})
            q_name = entry.get('title')
            if q_name:
                if q_name not in existing_q_map:
                    existing_q_map[q_name] = []
                existing_q_map[q_name].append(item)

        # Load tracked item IDs from sync map
        tracked_item_ids = {}
        if map_entry and isinstance(map_entry, dict) and 'item_ids' in map_entry:
            tracked_item_ids = map_entry['item_ids']

        new_tracked_item_ids = {}

        for i, q_data in enumerate(questions_data):
            q_name = q_data.get('question_name', f"Question {i+1}")

            item_data = self._transform_question(q_data, i + 1)

            # 1. Try to match by tracked ID first (fastest/safest)
            item_id = tracked_item_ids.get(q_name)

            # 2. If no tracked ID, try to match by name (fallback for missing cache)
            if not item_id and q_name in existing_q_map and len(existing_q_map[q_name]) > 0:
                 # Take the first matching item to adopt
                 adopted_item = existing_q_map[q_name].pop(0)
                 item_id = adopted_item.get('id')
                 logger.debug("    Adopted existing question: %s", q_name)

            # 3. If we still have an item_id but it's in the existing map under this name,
            #    we should remove it from the map so it isn't deleted during cleanup.
            if item_id and q_name in existing_q_map:
                 # Filter out the item we're keeping
                 existing_q_map[q_name] = [item for item in existing_q_map[q_name] if item.get('id') != item_id]

            if item_id:
                # Update existing
                logger.debug("    Updating question: %s", q_name)
                try:
                    updated_item = client.update_item(course_id, assignment_id, item_id, item_data)
                    new_tracked_item_ids[q_name] = item_id
                except Exception as e:
                     logger.error("    Failed to update question %s: %s. Re-creating.", q_name, e)
                     created_item = client.create_item(course_id, assignment_id, item_data)
                     new_tracked_item_ids[q_name] = str(created_item['id'])
            else:
                # Create new
                logger.info("    [green]Adding new question:[/green] %s", q_name)
                created_item = client.create_item(course_id, assignment_id, item_data)
                new_tracked_item_ids[q_name] = str(created_item['id'])

        # 4. Cleanup remaining orphaned or duplicated items on Canvas
        for q_name, items_list in existing_q_map.items():
             for item in items_list:
                  logger.info("    [red]Deleting orphaned question:[/red] %s", q_name)
                  try:
                      client.delete_item(course_id, assignment_id, item['id'])
                  except Exception as e:
                      logger.error("      Failed to delete question: %s", e)

        # Save map
        if content_root:
            rel_path = os.path.relpath(file_path, content_root).replace('\\', '/')
            sync_map = load_sync_map(content_root)
            sync_map[rel_path] = {
                'id': str(assignment_id),
                'mtime': mtime,
                'item_ids': new_tracked_item_ids
            }
            save_sync_map(content_root, sync_map)

    def _transform_question(self, q_data, position):
        """ Transforms internal question representation to New Quizzes API payload. """
        q_type = q_data.get('question_type', 'multiple_choice_question')

        interaction_slug = 'choice'
        if q_type == 'true_false_question':
            interaction_slug = 'true-false'
        elif q_type == 'multiple_answers_question':
            interaction_slug = 'multi-answer'
        elif q_type == 'numeric_question':
            interaction_slug = 'numeric'
        elif q_type == 'formula_question':
            interaction_slug = 'formula'

        # Scoring algorithm per Canvas New Quizzes API:
        # choice / true-false -> "Equivalence"
        # multi-answer -> "AllOrNothing"
        # numeric / formula -> "Numeric" (enables autograding)
        # essay / file-upload -> "None" (manual grading)
        scoring_algorithm = "Equivalence"
        if interaction_slug == 'multi-answer':
            scoring_algorithm = "AllOrNothing"
        elif interaction_slug in ('numeric', 'formula'):
            scoring_algorithm = "Numeric"

        item_data = {
            "entry_type": "Item",
            "position": position,
            "points_possible": float(q_data.get('points_possible', 1.0)),
            "properties": {},
            "entry": {
                "title": q_data.get('question_name', f"Question {position}"),
                "item_body": q_data.get('question_text', ''),
                "interaction_type_slug": interaction_slug,
                "scoring_algorithm": scoring_algorithm,
                "calculator_type": "none",
                "interaction_data": {},
                "scoring_data": {},
                "feedback": {}
            }
        }

        # Feedback
        if 'correct_comments' in q_data:
            item_data['entry']['feedback']['correct'] = q_data['correct_comments']
        if 'incorrect_comments' in q_data:
            item_data['entry']['feedback']['incorrect'] = q_data['incorrect_comments']

        # Answers
        answers = q_data.get('answers', [])

        if interaction_slug in ['choice', 'multi-answer']:
            choices = []
            correct_values = []

            for index, ans in enumerate(answers):
                choice_id = str(uuid.uuid4())
                ans_text = ans.get('answer_html') or ans.get('answer_text', str(index))

                choice = {
                    "id": choice_id,
                    "position": index + 1,
                    "itemBody": ans_text
                }
                choices.append(choice)

                # Check if correct (Classic uses weight=100)
                if ans.get('weight', 0) == 100 or ans.get('answer_weight', 0) == 100:
                    correct_values.append(choice_id)

            item_data['entry']['interaction_data']['choices'] = choices

            if interaction_slug == 'choice':
                if correct_values:
                    item_data['entry']['scoring_data']['value'] = correct_values[0]
            elif interaction_slug == 'multi-answer':
                item_data['entry']['scoring_data']['value'] = correct_values

        elif interaction_slug == 'true-false':
            item_data['entry']['interaction_data']['true_choice'] = 'True'
            item_data['entry']['interaction_data']['false_choice'] = 'False'

            correct_value = False
            for ans in answers:
                if ans.get('weight', 0) == 100 or ans.get('answer_weight', 0) == 100:
                    ans_text = str(ans.get('answer_text', '')).lower()
                    if 'true' in ans_text or 't' == ans_text or 'rätt' == ans_text:
                        correct_value = True
                    break

            item_data['entry']['scoring_data']['value'] = correct_value

        elif interaction_slug == 'numeric':
            scoring_values = []
            for ans in answers:
                if ans.get('answer_weight', 100) == 0:
                    continue  # only correct numeric answers

                ans_id = str(uuid.uuid4())
                ans_obj = {"id": ans_id}

                if 'start' in ans and 'end' in ans:
                    ans_obj['type'] = 'withinARange'
                    ans_obj['start'] = str(ans['start'])
                    ans_obj['end'] = str(ans['end'])
                elif 'margin' in ans:
                    ans_obj['type'] = 'marginOfError'
                    ans_obj['value'] = str(ans.get('value', '0'))
                    ans_obj['margin'] = str(ans['margin'])
                    ans_obj['margin_type'] = str(ans.get('margin_type', 'absolute'))
                elif 'precision' in ans:
                    ans_obj['type'] = 'preciseResponse'
                    ans_obj['value'] = str(ans.get('value', '0'))
                    ans_obj['precision'] = str(ans['precision'])
                    ans_obj['precision_type'] = str(ans.get('precision_type', 'decimals'))
                else:
                    ans_obj['type'] = 'exactResponse'
                    ans_obj['value'] = str(ans.get('value', '0'))

                scoring_values.append(ans_obj)

            item_data['entry']['scoring_data']['value'] = scoring_values

        elif interaction_slug == 'formula':
            formula = str(q_data.get('formula', '0'))
            variables = q_data.get('variables', [])
            answer_count = int(q_data.get('answer_count', 10))

            margin = str(q_data.get('margin', '0'))
            margin_type = str(q_data.get('margin_type', 'absolute'))
            numeric_config = {
                "type": "marginOfError",
                "margin": margin,
                "margin_type": margin_type
            }

            api_vars = []
            for v in variables:
                api_vars.append({
                    "name": v.get('name'),
                    "min": str(v.get('min', '0')),
                    "max": str(v.get('max', '10')),
                    "precision": int(v.get('precision', 0))
                })

            distribution = str(q_data.get('distribution', 'random'))

            generated_solutions = self._generate_formula_solutions(formula, variables, answer_count, distribution)

            item_data['entry']['scoring_data']['value'] = {
                "formula": formula,
                "numeric": numeric_config,
                "variables": api_vars,
                "answer_count": str(answer_count),
                "generated_solutions": generated_solutions
            }

        return item_data

    def _generate_formula_solutions(self, formula, variables, count, distribution='random'):
        import random
        import math
        try:
            from asteval import Interpreter
        except ImportError:
            raise ImportError("The 'asteval' library is required to sync formula questions. Please run `uv pip install asteval`")

        aeval = Interpreter()
        solutions = []

        # Pre-compute evenly spaced values per variable if distribution == 'even'
        even_values = {}
        if distribution == 'even':
            for v in variables:
                name = v.get('name')
                vmin = float(v.get('min', 0))
                vmax = float(v.get('max', 10))
                prec = int(v.get('precision', 0))

                if prec == 0:
                    # Integer steps: pick count evenly spaced integers
                    step = (vmax - vmin) / (count - 1) if count > 1 else 0
                    vals = [float(round(vmin + i * step)) for i in range(count)]
                else:
                    step = (vmax - vmin) / (count - 1) if count > 1 else 0
                    vals = [round(vmin + i * step, prec) for i in range(count)]
                even_values[name] = vals

        for iteration in range(count):
            inputs = []
            for v in variables:
                name = v.get('name')
                vmin = float(v.get('min', 0))
                vmax = float(v.get('max', 10))
                prec = int(v.get('precision', 0))

                if distribution == 'even':
                    val = even_values[name][iteration]
                else:
                    if prec == 0:
                        val = float(random.randint(int(vmin), int(vmax)))
                    else:
                        val = round(random.uniform(vmin, vmax), prec)

                inputs.append({"name": name, "value": str(val)})

            # Clear errors from previous iterations
            aeval.error = []

            for i_var in inputs:
                aeval.symtable[i_var['name']] = float(i_var['value'])

            output_val = aeval(formula)
            if aeval.error:
                error_msgs = "\n".join(str(e) for e in aeval.error)
                raise ValueError(f"Formula evaluation error for '{formula}':\n{error_msgs}")

            # Guard against division by zero or invalid results
            if output_val is None or (isinstance(output_val, float) and (math.isnan(output_val) or math.isinf(output_val))):
                raise ValueError(f"Formula '{formula}' produced invalid result ({output_val}) with inputs: {inputs}")

            output_str = str(round(output_val, 4)) if isinstance(output_val, float) else str(output_val)

            solutions.append({
                "inputs": inputs,
                "output": output_str
            })

        return solutions
