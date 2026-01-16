import json
import os
from handlers.base_handler import BaseHandler
from handlers.content_utils import get_mapped_id, save_mapped_id, parse_module_name

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

        # 2. Find/Create Quiz
        existing_quiz = None

        # 2a. Try ID lookup via Sync Map
        if content_root:
            mapped_id = get_mapped_id(content_root, file_path)
            if mapped_id:
                try:
                    existing_quiz = course.get_quiz(mapped_id)
                except:
                    print(f"    ! Mapped Quiz ID {mapped_id} not found in Canvas. Falling back to search.")

        # 2b. Fallback to Title Search
        if not existing_quiz:
            quizzes = course.get_quizzes(search_term=title)
            for q in quizzes:
                if q.title == title:
                    existing_quiz = q
                    break
        
        quiz_payload = {
            'title': title,
            'quiz_type': 'practice_quiz',
            'published': published
        }

        if existing_quiz:
            print(f"    -> Updating quiz: {title} (ID: {existing_quiz.id})")
            existing_quiz.edit(quiz=quiz_payload)
            quiz_obj = existing_quiz
        else:
            print(f"    -> Creating quiz: {title}")
            quiz_obj = course.create_quiz(quiz=quiz_payload)

        # 2c. Update Sync Map
        if content_root:
            save_mapped_id(content_root, file_path, quiz_obj.id)

        # 3. Add/Update Questions
        # ... (Remaining logic same) ...
        print(f"    -> Syncing {len(questions_data)} questions...")
        existing_questions = list(quiz_obj.get_questions())
        existing_q_map = {q.question_name: q for q in existing_questions}
        
        for q_data in questions_data:
            q_name = q_data.get('question_name')
            if q_name in existing_q_map:
                existing_q = existing_q_map[q_name]
                
                # Safer Comparison logic
                needs_update = False
                
                # 1. Text check
                if getattr(existing_q, 'question_text', '') != q_data.get('question_text', ''):
                    needs_update = True
                
                # 2. Points check
                elif getattr(existing_q, 'points_possible', 0) != q_data.get('points_possible', 0):
                    needs_update = True
                
                # 3. Type check
                elif getattr(existing_q, 'question_type', '') != q_data.get('question_type', ''):
                    needs_update = True
                
                # 4. Answers check (basic comparison)
                # Canvas API returns answers as a list of dicts. 
                # Local q_data also has answers as a list of dicts.
                elif getattr(existing_q, 'answers', []) != q_data.get('answers', []):
                    needs_update = True
                
                if needs_update:
                    print(f"    -> Updating question: {q_name}")
                    existing_q.edit(question=q_data)
                else:
                    pass # Already matches
            else:
                print(f"    + Adding new question: {q_name}")
                quiz_obj.create_question(question=q_data)

        # 4. Add to Module
        if module:
            self.add_to_module(module, {
                'type': 'Quiz',
                'content_id': quiz_obj.id,
                'title': quiz_obj.title,
                'published': published
            }, indent=indent)
