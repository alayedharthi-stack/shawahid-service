# ── Shawahid Service — Production Dockerfile ─────────────────────────────────
# Fully independent from Nahla's Dockerfile — do NOT merge or share layers.
#
# Build stages:
#   1. Install Python deps
#   2. Install Playwright Chromium with all system dependencies
#   3. Copy app code + start script
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim AS base

# System dependencies for Playwright/Chromium and psycopg2
# Using --with-deps at playwright install time handles most of these,
# but we pre-install the minimal set for faster layer caching.
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Chromium shared libraries
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libxshmfence1 \
    libdbus-1-3 \
    libglib2.0-0 \
    # Audio (Chromium optional but avoids runtime warnings)
    libasound2 \
    # Utilities
    wget \
    ca-certificates \
    # Arabic font support for PDF rendering
    fonts-noto \
    fonts-noto-color-emoji \
    fonts-noto-cjk \
    # Curl for healthcheck debugging inside container
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python dependencies ───────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ── Playwright Chromium ───────────────────────────────────────────────────────
# --with-deps handles any remaining system libraries Chromium needs.
RUN playwright install --with-deps chromium

# ── Application code ──────────────────────────────────────────────────────────
COPY . .

# ── Storage directory (overridden by Railway Volume mount at /app/storage) ───
RUN mkdir -p /app/storage/teachers

# ── Start script ──────────────────────────────────────────────────────────────
RUN chmod +x /app/start.sh

EXPOSE 8010

CMD ["/app/start.sh"]
