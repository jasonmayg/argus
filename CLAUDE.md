Add at the top of CLAUDE.md under a ## Core Rules section so it applies to every session\n\nWhen I ask you to implement something, write the actual code immediately. Do NOT stop at producing implementation plans, architecture documents, or exploration summaries. Skip the planning phase and go straight to code changes unless I explicitly ask for a plan first.

Add under ## Code Style or ## Core Rules section\n\nAll code, comments, variable names, UI text, docstrings, and documentation must be in English. Never generate code or content in Chinese or any other non-English language.

Add under ## Core Rules or ## Output Preferences section\n\nDo not generate summary documents, recap files, or 'final summary' markdown unless explicitly asked. Focus output on deliverables: working code, tests, and commits.

Add under a ## Project Context section near the top of CLAUDE.md\n\nThis is a Python RAG/Multi-Agent project using LangChain, LangGraph, Streamlit, ChromaDB, and SQLite. Always ensure .env files are loaded (via python-dotenv) before any API client initialization. When modifying database code, handle SQLite threading with check_same_thread=False.
