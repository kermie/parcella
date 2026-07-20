FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libcairo2 \
    libffi-dev shared-mime-info fonts-dejavu-core \
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
