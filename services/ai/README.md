# services/ai

FastAPI AI analysis service.

## Setup

```bash
cd services/ai
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Run

```bash
uvicorn src.main:app --reload --host 0.0.0.0 --port ${AI_SERVICE_PORT:-8000}
```
