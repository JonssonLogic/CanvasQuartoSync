import json
import os
from handlers.base_handler import BaseHandler

class QuizHandler(BaseHandler):
    def can_handle(self, file_path: str) -> bool:
        return file_path.endswith('.json') and 'Quiz' in file_path

    def sync(self, file_path: str, course, module=None):
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
            title = os.path.splitext(filename)[0]
            parts = title.split('_', 1)
            if len(parts) > 1 and parts[0].isdigit():
                title = parts[1].replace('_', ' ')
        
        # Metadata
        published = canvas_meta.get('published', False)
        indent = canvas_meta.get('indent', 0)

        # 2. Find/Create Quiz
        existing_quiz = None
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
            print(f"    -> Updating quiz: {title}")
            existing_quiz.edit(quiz=quiz_payload)
            quiz_obj = existing_quiz
        else:
            print(f"    -> Creating quiz: {title}")
            quiz_obj = course.create_quiz(quiz=quiz_payload)

        # 3. Add/Update Questions
        print(f"    -> Syncing {len(questions_data)} questions...")
        existing_questions = list(quiz_obj.get_questions())
        existing_q_names = {q.question_name for q in existing_questions}
        
        for q_data in questions_data:
            q_name = q_data.get('question_name')
            if q_name in existing_q_names:
                pass 
            else:
                quiz_obj.create_question(question=q_data)

        # 4. Add to Module
        if module:
            self.add_to_module(module, {
                'type': 'Quiz',
                'content_id': quiz_obj.id,
                'title': quiz_obj.title,
                'published': published
            }, indent=indent)
