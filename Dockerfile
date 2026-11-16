# Портал ЭКРОС — образ приложения
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings

WORKDIR /app

# psycopg[binary] не требует сборки, но libpq нужна для клиента PostgreSQL
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p var \
    && DJANGO_SECRET_KEY=build-only-key DJANGO_ALLOWED_HOSTS='*' \
       python manage.py collectstatic --noinput

# непривилегированный пользователь
RUN useradd -r -u 10001 -s /usr/sbin/nologin portal \
    && chown -R portal:portal /app
USER portal

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz/ || exit 1

CMD ["sh", "-c", "python manage.py migrate --noinput && \
     gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 60 \
     --access-logfile - --error-logfile -"]
