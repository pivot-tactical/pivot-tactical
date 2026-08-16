import re

with open("server/pivot/api/app.py", "r") as f:
    content = f.read()

# We will export the allowed origins so ws.py can import them.
content = content.replace(
    '        allow_origins=[',
    'ALLOW_ORIGINS = [\n    "http://localhost:5173",\n    "http://127.0.0.1:5173",\n    "http://localhost:8080",\n    "http://127.0.0.1:8080",\n]\nALLOW_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1|192\.168\.[0-9]+\.[0-9]+|10\.[0-9]+\.[0-9]+\.[0-9]+|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]+\.[0-9]+)(:[0-9]+)?\Z"\n\n' +
    '        allow_origins=ALLOW_ORIGINS,  # type: ignore\n        # allow_origins=[',
)
# Okay, doing this programmatically might be messy, let's use replace_with_git_merge_diff directly
