# syntax=docker/dockerfile:1
# Trading-domain backend (FastAPI). Dev image: deps baked in, app bind-mounted
# for uvicorn --reload hot reload.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Python + the data-service deps are needed so the backend can spawn
# services/data/backtester.py for UI-triggered backtests.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8000

# `migrate` runs Alembic first (adopting the existing schema on the first boot),
# then serves. Compose overrides this to add --reload in development.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
