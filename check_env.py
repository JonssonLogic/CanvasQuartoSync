import os
print("API_URL:", "Set" if os.environ.get("CANVAS_API_URL") else "Missing")
print("API_TOKEN:", "Set" if os.environ.get("CANVAS_API_TOKEN") else "Missing")
print("Course ID:", "1434 (Hardcoded in script)")
