# ── Shawahid Service — Production Dockerfile ─────────────────────────────────
# Uses the official Playwright Python image which ships with:
#   - Python 3.11
#   - Chromium pre-installed at /ms-playwright/
#   - All Chromium system dependencies already satisfied
# ─────────────────────────────────────────────────────────────────────────────

FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Arabic & emoji fonts for correct RTL PDF rendering
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto \
    fonts-noto-color-emoji \
    fonts-noto-cjk \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ───────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ── Application code ──────────────────────────────────────────────────────────
COPY . .

# Storage directory — overridden by Railway Volume at /app/storage
RUN mkdir -p /app/storage/teachers

# Fix CRLF line endings (created on Windows) and make executable
RUN sed -i 's/\r//' /app/start.sh && chmod +x /app/start.sh

EXPOSE 8010

# Using sh -c ensures POSIX variable expansion for PORT and handles CRLF safely
CMD ["sh", "/app/start.sh"]
