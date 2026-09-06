#!/bin/bash
# Runs after every Edit/Write; formats + auto-fixes the touched file if it's Python.

input=$(cat)
file_path=$(python3 -c "import json,sys; print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))" <<< "$input")

[[ "$file_path" == *.py ]] || exit 0

cd "${CLAUDE_PROJECT_DIR}" || exit 0
uv run ruff format "$file_path" 2>/dev/null
uv run ruff check --fix "$file_path" 2>/dev/null
exit 0
