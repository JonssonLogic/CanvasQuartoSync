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
        print(f"Syncing New Quiz: {filename}")
        
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
                print(f"    ! Error loading QMD New Quiz: {e}")
                return
        else:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                questions_data = data.get('questions', [])
                canvas_meta = data.get('canvas', {})
            except Exception as e:
                print(f"    ! Error loading JSON New Quiz: {e}")
                return

        title_override = canvas_meta.get('title')
        title = title_override if title_override else parse_module_name(os.path.splitext(filename)[0])
        indent = canvas_meta.get('indent', 0)
        published = canvas_meta.get('published', False)

        current_mtime = os.path.getmtime(file_path)
        existing_id, map_entry = get_mapped_id(content_root, file_path) if content_root else (None, None)
        
        needs_update = True
        quiz_obj = None

        # Check sync map for existing quiz
        if existing_id and isinstance(map_entry, dict):
            if map_entry.get('mtime') == current_mtime:
                print(f"    -> Skipping update (No changes detected).")
                needs_update = False

            # Always try to fetch the existing quiz when we have an ID,
            # regardless of whether the file changed or not.
            try:
                quiz_obj = client.get_quiz(course_id, existing_id)
            except Exception as e:
                print(f"    ! Cached New Quiz ID {existing_id} not found in Canvas. Re-creating.")
                quiz_obj = None
                needs_update = True

        # Build quiz payload
        quiz_payload = {
            'title': title,
            'published': published,
        }
        
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
        if 'omit_from_final_grade' in canvas_meta:
            quiz_payload['omit_from_final_grade'] = canvas_meta['omit_from_final_grade']
            
        # Quiz Settings
        if 'shuffle_answers' in canvas_meta:
            quiz_payload['shuffle_answers'] = canvas_meta['shuffle_answers']
        if 'shuffle_questions' in canvas_meta:
            quiz_payload['shuffle_questions'] = canvas_meta['shuffle_questions']
        if 'time_limit' in canvas_meta:
            quiz_payload['session_time_limit_in_seconds'] = canvas_meta['time_limit']

        # Handle multiple attempts mapping
        if 'allowed_attempts' in canvas_meta:
            attempts = canvas_meta['allowed_attempts']
            quiz_payload['multiple_attempts_enabled'] = attempts != 1
            if attempts != 1:
                quiz_payload['attempt_limit'] = attempts > 0
                if attempts > 0:
                    quiz_payload['allowed_attempts'] = attempts

        if needs_update:
            # Render question content through Quarto (LaTeX, markdown, images)
            base_path = os.path.dirname(file_path)
            questions_data = self._render_qmd_questions(questions_data, base_path, course, content_root)
            
            try:
                if quiz_obj:
                    print(f"    -> Updating New Quiz: {title} (ID: {existing_id})")
                    quiz_obj = client.update_quiz(course_id, existing_id, quiz_payload)
                else:
                    print(f"    -> Creating New Quiz: {title}")
                    quiz_obj = client.create_quiz(course_id, quiz_payload)
                    existing_id = str(quiz_obj['id'])
                    map_entry = None  # Clear stale item IDs — new quiz has no items yet
                
                # Sync questions
                self._sync_questions(client, course_id, existing_id, questions_data, content_root, file_path, current_mtime, map_entry)

            except NewQuizAPIError as e:
                print(f"    ! API Error: {e}")
                import traceback
                traceback.print_exc()
                return

        # Add to Module
        if module and existing_id:
            return self.add_to_module(module, {
                'type': 'Assignment',
                'content_id': existing_id,
                'title': title,
                'published': published
            }, indent=indent)

    def _render_qmd_questions(self, questions_data, base_path, course, content_root):
        """
        Render markdown/LaTeX content in quiz questions to HTML.
        Batches all content into a single Quarto render for performance.
        Uses <div id="qchunk-N"> markers to split the output back into pieces.
        """
        # Step 1: Collect all markdown pieces that need rendering
        chunks = []  # list of (key, markdown_text)
        
        for qi, q in enumerate(questions_data):
            if q.get('question_text'):
                chunks.append((f"q{qi}_text", q['question_text']))
            
            for ai, ans in enumerate(q.get('answers', [])):
                if ans.get('answer_html'):
                    chunks.append((f"q{qi}_a{ai}", ans['answer_html']))
                elif ans.get('answer_text'):
                    chunks.append((f"q{qi}_a{ai}", ans['answer_text']))
            
            for comment_key in ['correct_comments', 'incorrect_comments']:
                if q.get(comment_key):
                    chunks.append((f"q{qi}_{comment_key}", q[comment_key]))
        
        if not chunks:
            return questions_data
        
        print(f"    -> Rendering {len(questions_data)} questions through Quarto...")
        
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
            else:
                print(f"    ! Warning: Quarto render failed, using processed markdown.")
                rendered_map = processed_chunks
            
        except Exception as e:
            print(f"    ! Warning: Quarto render error: {e}")
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
        print(f"    -> Syncing {len(questions_data)} questions to New Quiz...")
        
        # Load existing items from Canvas
        existing_items_resp = client.list_items(course_id, assignment_id)
        existing_items = existing_items_resp if isinstance(existing_items_resp, list) else []

        # Load tracked item IDs from sync map
        tracked_item_ids = {}
        if map_entry and isinstance(map_entry, dict) and 'item_ids' in map_entry:
            tracked_item_ids = map_entry['item_ids']

        new_tracked_item_ids = {}

        for i, q_data in enumerate(questions_data):
            q_name = q_data.get('question_name', f"Question {i+1}")
            
            item_data = self._transform_question(q_data, i + 1)
            
            item_id = tracked_item_ids.get(q_name)
            
            if item_id:
                # Update existing
                print(f"    -> Updating question: {q_name}")
                try:
                    updated_item = client.update_item(course_id, assignment_id, item_id, item_data)
                    new_tracked_item_ids[q_name] = item_id
                except Exception as e:
                     print(f"    ! Error updating question {q_name}: {e}. Re-creating it.")
                     created_item = client.create_item(course_id, assignment_id, item_data)
                     new_tracked_item_ids[q_name] = str(created_item['id'])
            else:
                # Create new
                print(f"    + Adding new question: {q_name}")
                created_item = client.create_item(course_id, assignment_id, item_data)
                new_tracked_item_ids[q_name] = str(created_item['id'])
                
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

        # Scoring algorithm per official Canvas API docs:
        # choice / true-false -> "Equivalence"
        # multi-answer -> "AllOrNothing"
        scoring_algorithm = "Equivalence"
        if interaction_slug == 'multi-answer':
            scoring_algorithm = "AllOrNothing"
        elif interaction_slug in ('numeric', 'formula'):
            scoring_algorithm = "None"
            
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
