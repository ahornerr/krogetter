FROM python:3.12-slim

# Install system dependencies for Firefox/Camoufox + git for fork install
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgtk-3-0 libasound2 libdbus-glib-1-2 libx11-xcb1 \
    libxcomposite1 libxdamage1 libxrandr2 libxss1 \
    libxcursor1 libxinerama1 libxi6 libxtst6 \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libpango-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
    fonts-liberation git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install coryking fork of Camoufox (bypasses Akamai detection)
RUN pip install --no-cache-dir "camoufox @ git+https://github.com/coryking/camoufox.git@17015c647ac81d6ac1a34a82cfaf6736f5357658#subdirectory=pythonlib"

# Install Python dependencies
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir -e .

# Download Camoufox browser binary (~700MB)
RUN camoufox fetch

# Data directory
VOLUME ["/data"]
ENV KROGETTER_DATA_DIR=/data

EXPOSE 8585

CMD ["krogetter", "serve", "--host", "0.0.0.0", "--port", "8585"]
