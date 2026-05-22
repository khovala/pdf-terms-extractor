FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .

COPY scripts/ scripts/
COPY migrations/ migrations/

ENV PYTHONPATH=/app/src

CMD ["python", "scripts/worker.py"]
