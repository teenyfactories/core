FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "teenyfactories[all]"

WORKDIR /app

CMD ["python", "/app/script.py"]
