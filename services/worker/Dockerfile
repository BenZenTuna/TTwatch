# services/worker/Dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl build-essential libpq-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY services/worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Worker needs app.models for ORM — copy the API app models
COPY services/api/app /app/app
COPY services/worker/worker /app/worker

CMD ["celery", "-A", "worker.celeryconfig", "worker", "--loglevel=info"]
