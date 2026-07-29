# syntax=docker/dockerfile:1
# Data worker: candle ingestion → indicators → strategy scan, POSTing signal
# candidates to the API gate. Dev image: deps baked in, the whole package
# bind-mounted, and run under `watchfiles` so edits to strategies/* restart the
# loop automatically.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# libgomp1 is LightGBM's OpenMP runtime — it is NOT in python:*-slim, and
# without it `import lightgbm` dies with "libgomp.so.1: cannot open shared
# object file". Needed by services/data/training/.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt \
    && pip install watchfiles

COPY . .

# watchfiles reruns `python main.py` on any *.py change under the mounted tree.
CMD ["watchfiles", "python main.py"]
