# Temporary deployment image for the AI Study Assistant backend.
# Single-process (background study jobs use in-process asyncio tasks).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Fail the image build if the vendored Unicode PDF fonts are missing.
RUN test -s app/assets/fonts/DejaVuSans.ttf \
    && test -s app/assets/fonts/DejaVuSans-Bold.ttf

# Ephemeral-friendly defaults (Render free has no persistent disk).
# Override any of these with real environment variables at runtime.
ENV DATABASE_PATH=/tmp/study.db \
    TEMP_DIR=/tmp/uploads \
    OCR_TESSERACT_PATH=tesseract \
    HOST=0.0.0.0

EXPOSE 8000

# --no-access-log keeps query-string tokens out of Uvicorn access logs.
# --workers 1 is required: background study jobs use in-process asyncio tasks.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-access-log"]
