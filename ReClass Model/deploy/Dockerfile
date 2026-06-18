# ReClass API — production container

FROM python:3.12-slim

WORKDIR /app

# System deps for psycopg and health checks
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl libpq5 postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt
COPY api/requirements.txt ./api/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -r api/requirements.txt

COPY . .

ENV PYTHONPATH=/app
ENV RECLASS_API_ENV=production
ENV RECLASS_AUDIT_BACKEND=db

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://127.0.0.1:8000/health || exit 1

CMD ["python", "-m", "uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
