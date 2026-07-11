dev:
    uv run fastapi dev

run:
    uv run fastapi run main.py --host 0.0.0.0 --port 8000

format:
    ty check && ruff format
