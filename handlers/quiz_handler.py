import json
import os
import subprocess
import re
import frontmatter

from handlers.base_handler import BaseHandler
from handlers.content_utils import get_mapped_id, save_mapped_id, parse_module_name, process_content, safe_delete_file, safe_delete_dir
from handlers.qmd_quiz_parser import parse_qmd_quiz
from handlers.log import logger

class QuizHandler(BaseHandler):
    def can_handle(self, file_path: str) -> bool:
        # JSON quiz files
        if file_path.endswith('.json'):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                # New Format check
                if isinstance(data, dict) and 'questions' in data:
                    return True

                # Legacy Format check (list of questions)
                if isinstance(data, list) and len(data) > 0 and 'question_name' in data[0]:
                    return True

                return False
            except:
                return False

        # QMD quiz files
        if file_path.endswith('.qmd'):
            try:
                post = frontmatter.load(file_path)
                if post.metadata.get('canvas', {}).get('type') == 'quiz':
                    return True
            except:
                pass

            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read(4096)  # Read enough to check

                # Check for structural quiz components as a fallback
                # This ensures we still support .qmd quizzes that are missing the `canvas: type: quiz` frontmatter flag
                return ':::: {.question' in content or '::::{.question' in content
            except:
                return False

        return False

    def sync(self, file_path: str, course, module=None, canvas_obj=None, content_root=None):
        filename = os.path.basename(file_path)
        logger.info("  [cyan]Syncing quiz:[/cyan] [bold]%s[/bold]", filename)

        # 1. Load Data
        questions_data = []
        canvas_meta = {}
        title_override = None
        is_qmd = file_path.endswith('.qmd')

        if is_qmd:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    raw_content = f.read()
                canvas_meta, questions_data = parse_qmd_quiz(raw_content)
                title_override = canvas_meta.get('title')

            except Exception as e:
                logger.error("    Failed to load QMD quiz: %s", e)
                return
        else:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                if isinstance(data, dict) and 'questions' in data:
                    # New Format: {"canvas": {...}, "questions": [...]}
                    questions_data = data.get('questions', [])
                    canvas_meta = data.get('canvas', {})
                    title_override = canvas_meta.get('title')
                elif isinstance(data, list):
                    # Legacy Format: [...]
                    questions_data = data
                else:
                    logger.error("    Unrecognized JSON format. Expected 'questions' key or a list.")
                    return

            except Exception as e:
                logger.error("    Failed to load JSON quiz: %s", e)
                return



        # Title Logic
        if title_override:
            title = title_override
        else:
            title = parse_module_name(os.path.splitext(filename)[0])

        # Metadata
        published = canvas_meta.get('published', False)
        indent = canvas_meta.get('indent', 0)

        # 1b. Process Content (ALWAYS, to track ACTIVE_ASSET_IDS for pruning)
        # We discard the returned HTML because we only want the side-effect of
        # registering active assets to prevent them from being orphaned if no update is needed.
        base_path = os.path.dirname(file_path)
        _ = process_content(raw_content if is_qmd else json.dumps(data), base_path, course, content_root=content_root)

        # Process description_file if provided to track its assets too
        if 'description_file' in canvas_meta:
             desc_file_path = os.path.join(base_path, canvas_meta['description_file'])
             if os.path.exists(desc_file_path):
                 with open(desc_file_path, 'r', encoding='utf-8') as df:
                     desc_content = df.read()
                 _ = process_content(desc_content, base_path, course, content_root=content_root)


        # 2. Find/Create Quiz
        existing_quiz = None

        json_mtime = os.path.getmtime(file_path)
        desc_mtime = 0
        desc_file_path = None

        if 'description_file' in canvas_meta:
             desc_file_path = os.path.join(os.path.dirname(file_path), canvas_meta['description_file'])
             if os.path.exists(desc_file_path):
                 desc_mtime = os.path.getmtime(desc_file_path)

        # Compound mtime to detect changes in either file
        current_mtime = json_mtime + desc_mtime

        existing_id, map_entry = get_mapped_id(content_root, file_path) if content_root else (None, None)

        needs_update = True
        quiz_obj = None

        # 2a. Try ID lookup via Sync Map
        if existing_id:
            try:
                quiz_obj = course.get_quiz(existing_id)
                # Smart Sync: Skip if mtime matches
                if isinstance(map_entry, dict) and map_entry.get('mtime') == current_mtime:
                    logger.debug("    No changes detected, skipping update")
                    needs_update = False
            except:
                logger.warning("    Previously synced quiz not found in Canvas, searching by title")

        # 2b. Fallback to Title Search
        if not quiz_obj:
            quizzes = course.get_quizzes(search_term=title)
            for q in quizzes:
                if q.title == title:
                    quiz_obj = q
                    break

        if needs_update:
            # Render question/answer markdown content to HTML (for both QMD and JSON)
            # This fixes LaTeX rendering issues in JSON quizzes by passing them through Quarto.
            # Moved here to avoid rendering if the quiz is already up-to-date.
            try:
                base_path = os.path.dirname(file_path)
                questions_data = self._render_qmd_questions(
                    questions_data, base_path, course, content_root
                )
            except Exception as e:
                logger.warning("    Failed to render quiz content through Quarto: %s", e)

            # 1b. Render description_file if provided (Only if updating)
            description_html = None
            if desc_file_path and os.path.exists(desc_file_path):
                description_html = self._render_description_file(desc_file_path, course, content_root)
            elif 'description_file' in canvas_meta:
                logger.warning("    Description file not found: %s", canvas_meta['description_file'])

            quiz_payload = {
                'title': title,
                'quiz_type': canvas_meta.get('quiz_type', 'practice_quiz'),
                'published': published
            }

            # Optional Advanced Options
            setting_map = {
                'description': 'description',
                'due_at': 'due_at',
                'unlock_at': 'unlock_at',
                'lock_at': 'lock_at',
                'show_correct_answers': 'show_correct_answers',
                'shuffle_answers': 'shuffle_answers',
                'time_limit': 'time_limit',
                'allowed_attempts': 'allowed_attempts',
                'one_question_at_a_time': 'one_question_at_a_time',
                'cant_go_back': 'cant_go_back',
                'access_code': 'access_code'
            }

            for local_key, canvas_key in setting_map.items():
                if local_key in ['due_at', 'unlock_at', 'lock_at']:
                    # Source of Truth: Use empty string to explicitly clear dates in Canvas API
                    # (None values are ignored by the API, but '' clears the field)
                    quiz_payload[canvas_key] = canvas_meta.get(local_key) or ''
                elif local_key == 'description':
                    # description_file takes precedence over inline description
                    if description_html:
                        quiz_payload[canvas_key] = description_html
                    elif local_key in canvas_meta:
                        quiz_payload[canvas_key] = canvas_meta[local_key]
                elif local_key in canvas_meta:
                    quiz_payload[canvas_key] = canvas_meta[local_key]

            # 2c. Prepare Quiz
            # For quizzes without submissions: unpublish -> update -> republish
            # For quizzes with submissions: update in-place -> trigger quiz data regeneration

            # Save the desired final state
            target_published = quiz_payload['published']
            has_submissions = False

            if quiz_obj:
                logger.info("    [yellow]Updating quiz:[/yellow] %s", title)
                try:
                    # Try to unpublish (Draft Mode). This triggers generate_quiz_data on republish.
                    quiz_obj.edit(quiz={'published': False})
                    quiz_payload['published'] = False
                except Exception as e:
                    err_str = str(e)
                    if "Can't unpublish" in err_str:
                         logger.warning("    Quiz has submissions, skipping draft mode")
                         has_submissions = True
                         quiz_payload['published'] = True
                    else:
                         logger.warning("    Could not unpublish quiz: %s", e)
                         quiz_payload['published'] = True

                # Apply settings (description, time limit, etc.)
                quiz_obj.edit(quiz=quiz_payload)
            else:
                logger.info("    [green]Creating quiz:[/green] %s", title)
                quiz_payload['published'] = False
                quiz_obj = course.create_quiz(quiz=quiz_payload)

            # Restore target published state for later
            quiz_payload['published'] = target_published

            # 2c. Update Sync Map
            if content_root:
                save_mapped_id(content_root, file_path, quiz_obj.id, mtime=current_mtime)

            # 3. Add/Update Questions
            logger.info("    [cyan]Syncing %d questions...[/cyan]", len(questions_data))
            existing_questions = list(quiz_obj.get_questions())

            existing_q_map = {}
            for q in existing_questions:
                if q.question_name not in existing_q_map:
                    existing_q_map[q.question_name] = []
                existing_q_map[q.question_name].append(q)

            for q_data in questions_data:
                q_name = q_data.get('question_name')

                # Pop the first matching existing question to adopt
                if q_name and q_name in existing_q_map and len(existing_q_map[q_name]) > 0:
                    existing_q = existing_q_map[q_name].pop(0)

                    # Safer Comparison logic
                    q_needs_update = False

                    if getattr(existing_q, 'question_text', '') != q_data.get('question_text', ''):
                        q_needs_update = True
                    elif getattr(existing_q, 'points_possible', 0) != q_data.get('points_possible', 0):
                        q_needs_update = True
                    elif getattr(existing_q, 'question_type', '') != q_data.get('question_type', ''):
                        q_needs_update = True
                    elif getattr(existing_q, 'answers', []) != q_data.get('answers', []):
                        q_needs_update = True

                    if q_needs_update:
                        logger.debug("    Updating question: %s", q_name)
                        existing_q.edit(question=q_data)
                else:
                    logger.info("    [green]Adding new question:[/green] %s", q_name)
                    quiz_obj.create_question(question=q_data)

            # 3b. Cleanup remaining orphaned or duplicated items on Canvas
            for q_name, items_list in existing_q_map.items():
                for existing_q in items_list:
                    logger.info("    [red]Deleting orphaned question:[/red] %s", q_name)
                    try:
                        existing_q.delete()
                    except Exception as e:
                        logger.error("      Failed to delete question: %s", e)
        else:
            # Smart Sync skipped update, but we already have quiz_obj
            pass

        # 3b. Finalize Quiz
        if needs_update:
            if has_submissions:
                # Quiz has student submissions — Canvas API limitation:
                # The REST API cannot trigger generate_quiz_data for already-published
                # quizzes (it only fires on workflow_state transitions). The UI endpoint
                # requires SSO session auth. Provide a direct link for manual save.
                requester = canvas_obj._Canvas__requester
                quiz_url = f"{requester.original_url}/courses/{course.id}/quizzes/{quiz_obj.id}"
                logger.warning("    [yellow]Quiz has submissions. Please click 'Save It Now' in Canvas:[/yellow]\n      %s", quiz_url)
            else:
                # Normal flow: republish to trigger generate_quiz_data via state transition
                if quiz_payload['published']:
                    logger.info("    [green]Publishing quiz:[/green] %s", title)
                else:
                    logger.info("    [dim]Saving quiz as draft:[/dim] %s", title)
                try:
                    final_payload = {
                        'published': quiz_payload['published'],
                        'notify_of_update': True
                    }
                    quiz_obj.edit(quiz=final_payload)
                except Exception as e:
                    logger.warning("    Final save failed: %s", e)

        # 4. Add to Module
        if module:
            return self.add_to_module(module, {
                'type': 'Quiz',
                'content_id': quiz_obj.id,
                'title': quiz_obj.title,
                'published': published
            }, indent=indent)


    def _render_qmd_questions(self, questions_data, base_path, course, content_root):
        """
        Render markdown content in QMD quiz questions to HTML.

        Batches all markdown content into a single Quarto render for performance.
        Uses <div id="qchunk-N"> markers to split the rendered output back into
        individual pieces.
        """
        logger.debug("    Rendering %d questions through Quarto...", len(questions_data))

        # Step 1: Collect all markdown pieces that need rendering
        # Each entry: (piece_key, markdown_text)
        # piece_key is used to map the rendered HTML back to the question data
        chunks = []  # list of (key, markdown_text)

        for qi, q in enumerate(questions_data):
            if q.get('question_text'):
                chunks.append((f"q{qi}_text", q['question_text']))

            for ai, ans in enumerate(q.get('answers', [])):
                if ans.get('answer_html'):
                    chunks.append((f"q{qi}_a{ai}", ans['answer_html']))
                elif ans.get('answer_text'):
                    # Also render checklist answer_text through Quarto
                    # so that LaTeX and formatting work correctly
                    chunks.append((f"q{qi}_a{ai}", ans['answer_text']))

            for comment_key in ['correct_comments', 'incorrect_comments']:
                if q.get(comment_key):
                    chunks.append((f"q{qi}_{comment_key}", q[comment_key]))

        if not chunks:
            return questions_data

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
                        # Fallback: use processed markdown
                        rendered_map[key] = processed_chunks[key]
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
                        ans.pop('answer_text', None)  # Use HTML version instead
                    rendered_answers.append(ans)
                q['answers'] = rendered_answers

            for comment_key in ['correct_comments', 'incorrect_comments']:
                ck = f"q{qi}_{comment_key}"
                if ck in rendered_map:
                    q[comment_key] = rendered_map[ck]

            rendered_questions.append(q)

        return rendered_questions

    def _render_description_file(self, desc_file_path, course, content_root):
        """
        Renders a .qmd description file to HTML.
        Processes images/links and cleans up temp files.
        """
        filename = os.path.basename(desc_file_path)
        base_path = os.path.dirname(desc_file_path)

        logger.debug("    Rendering description: %s", filename)

        # Read and process content (uploads images, resolves links)
        with open(desc_file_path, 'r', encoding='utf-8') as f:
            raw_content = f.read()

        processed_content = process_content(raw_content, base_path, course, content_root=content_root)

        html_body = self.render_quarto_document(processed_content, base_path, f"desc_{filename}", content_root=content_root)
        if html_body is not None:
             return html_body.strip()
        return None
