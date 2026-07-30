FROM python:3.12-slim

WORKDIR /app

# postgresql-client-16, not just "postgresql-client": Debian trixie's own
# repo only ships v17, which is fine for pg_dump (backward-compatible with
# an older server) but NOT for a full restore round-trip -- a v17 pg_dump
# embeds a `SET transaction_timeout = 0;` preamble line (a v17-only GUC)
# that a v16 server rejects outright on psql restore. Pinning to the exact
# v16 client via the PGDG apt repo avoids this whole class of version-skew
# issue, matching the db/db_test services' PostgreSQL 16 exactly.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libcairo2 \
    libffi-dev shared-mime-info fonts-dejavu-core \
    ca-certificates gnupg lsb-release wget \
    && install -d /usr/share/postgresql-common/pgdg \
    && wget --quiet -O /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && sh -c 'echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list' \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-client-16 \
    && apt-get purge -y --auto-remove wget gnupg lsb-release \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x docker-entrypoint.sh

# Container als nicht-root-Benutzer laufen lassen, damit Dateien, die im
# Container erzeugt werden (z.B. neue Alembic-Migrationen), auf dem Host
# nicht root gehören. UID/GID 1000 entspricht dem ersten "normalen"
# Benutzer auf den meisten Linux-Systemen (per .env überschreibbar via
# docker compose build --build-arg).
ARG UID=1000
ARG GID=1000
RUN groupadd -g ${GID} appuser && useradd -u ${UID} -g ${GID} -m appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
