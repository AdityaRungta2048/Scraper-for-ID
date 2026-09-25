# Backend API + worker image (FastAPI, SQLAlchemy, RQ).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN pip install .

COPY backend/alembic.ini ./
COPY backend/alembic ./alembic

RUN useradd --create-home --uid 10001 matcher && mkdir -p /data && chown matcher /data
USER matcher

ENV DATA_DIR=/data
EXPOSE 8000
# Apply migrations, then serve. The worker service overrides the command.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
