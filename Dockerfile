# ── Shawahid Service — Production Dockerfile ─────────────────────────────────
# Uses the official Playwright Python image which ships with:
#   - Python 3.11
#   - Chromium pre-installed at /ms-playwright/
#   - All Chromium system dependencies already satisfied
# This avoids dependency conflicts on Debian trixie (python:3.11-slim).
# ─────────────────────────────────────────────────────────────────────────────

FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Install Arabic & emoji fonts for correct PDF rendering
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto \
    fonts-noto-color-emoji \
    fonts-noto-cjk \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ───────────────────────────────────────────────────────
COPY requirements.txt .
# Playwright Python SDK is pre-installed in base image.
# pip install will update it in-place; browsers remain at /ms-playwright/.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ── Application code ──────────────────────────────────────────────────────────
COPY . .

# Storage directory — overridden by Railway Volume at /app/storage
RUN mkdir -p /app/storage/teachers

# Fix line endings (in case of Windows CRLF) and make executable
RUN sed -i 's/\r//' /app/start.sh && chmod +x /app/start.sh

EXPOSE 8010

# Using sh -c avoids CRLF issues with the script on Windows-created repos
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8010} --workers 1"]
