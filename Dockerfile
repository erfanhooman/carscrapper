FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

# Install system deps for Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg ca-certificates \
    libnss3 libx11-6 libx11-xcb1 libxcomposite1 libxcursor1 libxdamage1 \
    libxrandr2 libxss1 libasound2 libpangocairo-1.0-0 libpango-1.0-0 \
    libgtk-3-0 libgbm1 libxshmfence1 libegl1 \
    fonts-dejavu fonts-liberation fonts-noto \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium for Playwright
RUN python -m playwright install --with-deps chromium

COPY . .

CMD ["python", "app.py"]
