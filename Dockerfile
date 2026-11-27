FROM python:3.12-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Cliente PostgreSQL 18 desde el repositorio oficial PGDG: el paquete de
# Debian trixie (17.x) no puede copiar un servidor PostgreSQL 18.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] http://apt.postgresql.org/pub/repos/apt trixie-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-18 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# collectstatic con variables de compilación ficticias: el manifiesto de
# WhiteNoise queda listo sin secretos ni datos reales en la imagen.
RUN DJANGO_SETTINGS_MODULE=sslviajes.settings \
    SECRET_KEY=build-only-0123456789abcdef0123456789abcdef0123456789abcdef \
    DEBUG=False \
    ALLOWED_HOSTS=localhost \
    CSRF_TRUSTED_ORIGINS=https://example.invalid \
    PUBLIC_BASE_URL=https://example.invalid \
    PRIVATE_STORAGE_BACKEND=s3 \
    PRIVATE_STORAGE_S3_BUCKET=build \
    PRIVATE_STORAGE_S3_ENDPOINT_URL=https://example.invalid \
    PRIVATE_STORAGE_S3_ACCESS_KEY_ID=build \
    PRIVATE_STORAGE_S3_SECRET_ACCESS_KEY=build \
    PRIVATE_STORAGE_S3_QUERYSTRING_EXPIRE=300 \
    BANK_TRANSFER_ACCOUNT_HOLDER=build \
    BANK_TRANSFER_ALIAS=build.demo \
    BANK_TRANSFER_CVU=0000000000000000000001 \
    BANK_TRANSFER_CUIT=20-00000000-1 \
    BANK_TRANSFER_ENTITY=build \
    ABUSE_HASH_SECRET=build \
    python manage.py collectstatic --noinput

ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "exec gunicorn sslviajes.wsgi --bind 0.0.0.0:${PORT}"]
