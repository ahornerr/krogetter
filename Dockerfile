FROM python:3.12-slim

# Install system dependencies for Firefox + Xvfb (invisible_playwright headless mode)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgtk-3-0 libasound2 libdbus-glib-1-2 libx11-xcb1 \
    libxcomposite1 libxdamage1 libxrandr2 libxss1 \
    libxcursor1 libxinerama1 libxi6 libxtst6 \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libpango-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
    fonts-liberation git xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies (cached unless pyproject.toml changes)
COPY pyproject.toml .
RUN mkdir -p src/krogetter && touch src/krogetter/__init__.py && \
    pip install --no-cache-dir -e .

# Download invisible_playwright Firefox binary (~100MB, cached unless deps change)
RUN python -m invisible_playwright fetch

# Copy actual source code (changes frequently, doesn't invalidate dep layers)
COPY src/ src/

# Data directory
VOLUME ["/data"]
ENV KROGETTER_DATA_DIR=/data

EXPOSE 8585

CMD ["krogetter", "serve", "--host", "0.0.0.0", "--port", "8585"]
