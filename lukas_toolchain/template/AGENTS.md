# Workflow rules
- Paths are relative to this repository root. Never write outside it.
- SPEC.md and CONTRACT.md are human-written input. Never change them.
- Write files with the write tool. Never answer with code or file content in chat.
- Write only the files your command names. Everything you need is given in the command.
- Hand-off is through files: docs/architecture/ -> docs/tickets/ -> src/ -> tests/ + docs/reports/ -> docs/.
- Do not run git. The toolchain (git-guard plugin) commits your files and switches branches.
- Keep answers short. End with one line: DONE: <files you wrote>.
