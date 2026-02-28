import requests
import json

class NewQuizAPIError(Exception):
    """Exception raised for errors in the New Quiz API calls."""
    def __init__(self, message, response=None):
        super().__init__(message)
        self.response = response

class NewQuizAPIClient:
    """
    A lightweight REST client for Canvas New Quizzes API.
    Uses the /api/quiz/v1/ base path which is completely separate from Classic Quizzes.
    """
    
    def __init__(self, api_url, api_token):
        # Ensure base URL doesn't have trailing slash
        self.base_url = api_url.rstrip('/')
        self.headers = {
            'Authorization': f'Bearer {api_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

    def _request(self, method, endpoint, **kwargs):
        """Helper to make API requests and handle errors."""
        url = f"{self.base_url}{endpoint}"
        try:
            response = requests.request(method, url, headers=self.headers, **kwargs)
            response.raise_for_status()
            
            # Delete requests usually return 204 No Content
            if response.status_code == 204:
                return {}
                
            return response.json()
        except requests.exceptions.RequestException as e:
            err_msg = f"API Request failed: {e}"
            if hasattr(e, 'response') and e.response is not None:
                try:
                    err_msg += f" - Response: {e.response.text}"
                except:
                    pass
            raise NewQuizAPIError(err_msg, response=getattr(e, 'response', None))

    # --- Quiz Actions ---

    def create_quiz(self, course_id, quiz_data):
        """
        Creates a New Quiz.
        POST /api/quiz/v1/courses/:course_id/quizzes
        """
        endpoint = f"/api/quiz/v1/courses/{course_id}/quizzes"
        return self._request('POST', endpoint, json={"quiz": quiz_data})

    def update_quiz(self, course_id, assignment_id, quiz_data):
        """
        Updates an existing New Quiz.
        PATCH /api/quiz/v1/courses/:course_id/quizzes/:assignment_id
        """
        endpoint = f"/api/quiz/v1/courses/{course_id}/quizzes/{assignment_id}"
        return self._request('PATCH', endpoint, json={"quiz": quiz_data})

    def get_quiz(self, course_id, assignment_id):
        """
        Gets a New Quiz.
        GET /api/quiz/v1/courses/:course_id/quizzes/:assignment_id
        """
        endpoint = f"/api/quiz/v1/courses/{course_id}/quizzes/{assignment_id}"
        return self._request('GET', endpoint)

    # --- Item Actions ---

    def list_items(self, course_id, assignment_id):
        """
        Lists items (questions, etc.) for a New Quiz.
        GET /api/quiz/v1/courses/:course_id/quizzes/:assignment_id/items
        """
        endpoint = f"/api/quiz/v1/courses/{course_id}/quizzes/{assignment_id}/items"
        return self._request('GET', endpoint)

    def create_item(self, course_id, assignment_id, item_data):
        """
        Creates a question item in a New Quiz.
        POST /api/quiz/v1/courses/:course_id/quizzes/:assignment_id/items
        """
        endpoint = f"/api/quiz/v1/courses/{course_id}/quizzes/{assignment_id}/items"
        # The payload structure is expected to be wrapped in an "item" object
        payload = {"item": item_data}
        return self._request('POST', endpoint, json=payload)

    def update_item(self, course_id, assignment_id, item_id, item_data):
        """
        Updates a question item in a New Quiz.
        PATCH /api/quiz/v1/courses/:course_id/quizzes/:assignment_id/items/:item_id
        """
        endpoint = f"/api/quiz/v1/courses/{course_id}/quizzes/{assignment_id}/items/{item_id}"
        payload = {"item": item_data}
        return self._request('PATCH', endpoint, json=payload)

    def delete_item(self, course_id, assignment_id, item_id):
        """
        Deletes a question item from a New Quiz.
        DELETE /api/quiz/v1/courses/:course_id/quizzes/:assignment_id/items/:item_id
        """
        endpoint = f"/api/quiz/v1/courses/{course_id}/quizzes/{assignment_id}/items/{item_id}"
        return self._request('DELETE', endpoint)
