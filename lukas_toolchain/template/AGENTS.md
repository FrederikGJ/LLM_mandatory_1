# Workflow rules
- The project root is this repository. Always use paths relative to it (e.g. docs/tickets/T-001.md). Never write outside it.
- SPEC.md is the product owner's input. Never change it.
- Hand off work through files only: docs/architecture/ -> docs/tickets/ -> code -> docs/reports/ -> docs/.
- Read only the files your task needs. Never read the whole repo.
- Git: when your task is done, commit your own changes on the current branch:
    git add <the files you changed>
    git commit -m "<your role>: <short summary>"
  To undo your own last commit: git revert --no-edit HEAD
  Never run any other git command. Branches and merges are handled by run_all.sh.
- Keep answers short. End with the list of changed files.
