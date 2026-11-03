# Бэкенд: Django + DRF + WebSocket-чат (daphne, ASGI)
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN chmod +x docker/backend/entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["docker/backend/entrypoint.sh"]
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "--proxy-headers", "config.asgi:application"]
