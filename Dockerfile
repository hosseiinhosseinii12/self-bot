FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH=/app/data

# Install Xray-core
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -L -o /tmp/xray.zip \
        https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip \
    && unzip /tmp/xray.zip -d /usr/local/bin/ \
    && chmod +x /usr/local/bin/xray \
    && rm /tmp/xray.zip

WORKDIR /app
COPY requirements.txt .

# Install deps, then ALWAYS upgrade telethon to the latest version
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --upgrade -r requirements.txt \
    && pip install --no-cache-dir --upgrade --no-deps telethon \
    && python -c "import telethon; print('telethon version:', telethon.__version__)"

COPY . .
RUN mkdir -p /app/data /app/data/logs /app/data/backups

EXPOSE 8080

CMD ["sh", "-c", "xray run -c /app/xray.json & sleep 2 && gunicorn --bind 0.0.0.0:$PORT --workers 1 --timeout 120 app:flask_app"]