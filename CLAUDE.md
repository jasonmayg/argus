## Project Context\n\n
This is a Python Multi-Agent project. Always ensure .env files are loaded (via python-dotenv) before any API client initialization. When modifying database code, handle SQLite threading with check_same_thread=False.

## Core Rules\n
When I ask you to implement something, write the actual code immediately. Do NOT stop at producing implementation plans, architecture documents, or exploration summaries. Skip the planning phase and go straight to code changes unless I explicitly ask for a plan first.

## Code Style\n\n
All code, comments, variable names, UI text, docstrings, and documentation must be in English. Never generate code or content in Chinese or any other non-English language.

## Output Preferences section\n\n
Do not generate summary documents, recap files, or 'final summary' markdown unless explicitly asked. Focus output on deliverables: working code, tests, and commits.
Make sure all generated docs except README.md are under docs/folder

## Development Commands
- Check syntax: `python -m py_compile $FILENAME` (Run this after any python file edit)

