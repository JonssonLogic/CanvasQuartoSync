import json
import os
import subprocess
import re
from handlers.base_handler import BaseHandler
from handlers.content_utils import get_mapped_id, save_mapped_id, parse_module_name, process_content, safe_delete_file, safe_delete_dir

class QuizHandler(BaseHandler):
    def can_handle(self, file_path: str) -> bool:
        if not file_path.endswith('.json'):
            return False
        
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

    def sync(self, file_path: str, course, module=None, canvas_obj=None, content_root=None):
        filename = os.path.basename(file_path)
        print(f"Syncing Quiz: {filename}")
        
        # 1. Load Data
        questions_data = []
        canvas_meta = {}
        title_override = None
        
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
                print("    ! Check JSON format.")
                return
                
        except Exception as e:
            print(f"    ! Error loading JSON: {e}")
            return

        # Title Logic
        if title_override:
            title = title_override
        else:
            title = parse_module_name(os.path.splitext(filename)[0])
        
        # Metadata
        published = canvas_meta.get('published', False)
        indent = canvas_meta.get('indent', 0)
        
        # 1b. Process description_file if provided - MOVED INSIDE needs_update
        base_path = os.path.dirname(file_path)


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
                    print(f"    -> Skipping update (No changes detected).")
                    needs_update = False
            except:
                print(f"    ! Mapped Quiz ID {existing_id} not found in Canvas. Falling back to search.")

        # 2b. Fallback to Title Search
        if not quiz_obj:
            quizzes = course.get_quizzes(search_term=title)
            for q in quizzes:
                if q.title == title:
                    quiz_obj = q
                    break
        
        if needs_update:
            # 1b. Render description_file if provided (Only if updating)
            description_html = None
            if desc_file_path and os.path.exists(desc_file_path):
                description_html = self._render_description_file(desc_file_path, course, content_root)
            elif 'description_file' in canvas_meta:
                print(f"    ! Description file not found: {canvas_meta['description_file']}")

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

            if quiz_obj:
                print(f"    -> Updating quiz: {title} (ID: {quiz_obj.id})")
                quiz_obj.edit(quiz=quiz_payload)
            else:
                print(f"    -> Creating quiz: {title}")
                quiz_obj = course.create_quiz(quiz=quiz_payload)

            # 2c. Update Sync Map
            if content_root:
                save_mapped_id(content_root, file_path, quiz_obj.id, mtime=current_mtime)

            # 3. Add/Update Questions
            print(f"    -> Syncing {len(questions_data)} questions...")
            existing_questions = list(quiz_obj.get_questions())
            existing_q_map = {q.question_name: q for q in existing_questions}
            
            for q_data in questions_data:
                q_name = q_data.get('question_name')
                if q_name in existing_q_map:
                    existing_q = existing_q_map[q_name]
                    
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
                        print(f"    -> Updating question: {q_name}")
                        existing_q.edit(question=q_data)
                else:
                    print(f"    + Adding new question: {q_name}")
                    quiz_obj.create_question(question=q_data)
        else:
            # Smart Sync skipped update, but we already have quiz_obj
            pass

        # 4. Add to Module
        if module:
            self.add_to_module(module, {
                'type': 'Quiz',
                'content_id': quiz_obj.id,
                'title': quiz_obj.title,
                'published': published
            }, indent=indent)

    def _render_description_file(self, desc_file_path, course, content_root):
        """
        Renders a .qmd description file to HTML.
        Processes images/links and cleans up temp files.
        """
        filename = os.path.basename(desc_file_path)
        base_path = os.path.dirname(desc_file_path)
        
        print(f"    -> Rendering description: {filename}")
        
        # Read and process content (uploads images, resolves links)
        with open(desc_file_path, 'r', encoding='utf-8') as f:
            raw_content = f.read()
        
        processed_content = process_content(raw_content, base_path, course, content_root=content_root)
        
        # Create temp file for Quarto render
        temp_qmd = os.path.join(base_path, f"_temp_desc_{filename}")
        temp_stem = os.path.splitext(f"_temp_desc_{filename}")[0]
        temp_html = temp_qmd.replace('.qmd', '.html')
        temp_files_dir = os.path.join(base_path, f"{temp_stem}_files")
        
        try:
            with open(temp_qmd, 'w', encoding='utf-8') as f:
                f.write(processed_content)
            
            cmd = ["quarto", "render", temp_qmd, "--to", "html"]
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            if not os.path.exists(temp_html):
                print(f"    ! Error: Expected HTML output from description render.")
                self._cleanup(temp_qmd, None, temp_files_dir)
                return None
            
            with open(temp_html, 'r', encoding='utf-8') as f:
                full_html = f.read()
            
            # Extract main content (same as assignment_handler)
            main_match = re.search(r'<main[^>]*id="quarto-document-content"[^>]*>(.*?)</main>', full_html, re.DOTALL)
            
            if main_match:
                html_body = main_match.group(1)
                html_body = re.sub(r'<header[^>]*id="title-block-header"[^>]*>.*?</header>', '', html_body, flags=re.DOTALL)
            else:
                html_body = full_html
                html_body = re.sub(r'<header[^>]*id="title-block-header"[^>]*>.*?</header>', '', html_body, flags=re.DOTALL)
            
            # Cleanup temp files
            self._cleanup(temp_qmd, temp_html, temp_files_dir)
            
            return html_body.strip()
            
        except Exception as e:
            print(f"    ! Error rendering description: {e}")
            self._cleanup(temp_qmd, temp_html, temp_files_dir)
            return None

    def _cleanup(self, qmd_path, html_path, files_dir):
        """Clean up temporary files from Quarto render."""
        if qmd_path:
            safe_delete_file(qmd_path)
        if html_path:
            safe_delete_file(html_path)
        if files_dir:
            safe_delete_dir(files_dir)
