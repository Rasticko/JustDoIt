# JustDoIt Textual (Python)

A local-only Python implementation of the JustDoIt TUI using [Textual](https://github.com/Textualize/textual).

## Scope
- Fully local SQLite app
- No Google Calendar integration

## Run
```bash
cd /home/jeppe/Dev/Justdoit-python
python -m venv .venv
source .venv/bin/activate
pip install -e .
justdoit-textual
```

For `fish` shell:
```fish
cd /home/jeppe/Dev/Justdoit-python
source .venv/bin/activate.fish
justdoit-textual
```

## Keybindings
- `j` / `k`: move selection
- `Tab`: switch focus sidebar/main
- `n`: new task
- `e`: edit task title
- `t`: edit task tags
- `v`: edit task attendees
- `d`: edit task deadline (`YYYY-MM-DD`, `tomorrow 14:00`, `next friday 9am`)
- `space`: toggle task done/todo
- `x`: trash task (or delete permanently from Trash)
- `u`: restore task from Trash
- `a`: new area
- `p`: new project (in selected area/project)
- `r`: rename area/project
- `q`: quit
