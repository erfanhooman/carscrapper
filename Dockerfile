FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# System deps for Chromium + fonts
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg ca-certificates \
    libnss3 libx11-6 libx11-xcb1 libxcomposite1 libxcursor1 libxdamage1 \
    libxrandr2 libxss1 libasound2 libpangocairo-1.0-0 libpango-1.0-0 \
    libgtk-3-0 libgbm1 libxshmfence1 libegl1 libopus0 \
    fonts-dejavu fonts-freefont-ttf fonts-liberation fonts-noto \
    && rm -rf /var/lib/apt/lists/*

# App
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt && \
    python -m playwright install --with-deps chromium

COPY app.py .

# Render/Heroku-style $PORT
ENV PORT=8000 HOST=0.0.0.0 HEADLESS=1
CMD exec uvicorn app:api --host ${HOST} --port ${PORT}
