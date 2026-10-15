# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /build
COPY requirements.txt .
RUN pip install -r requirements.txt

FROM builder AS test
COPY requirements-test.txt .
RUN pip install -r requirements-test.txt
WORKDIR /app
COPY . .
CMD ["pytest", "-v"]

FROM python:3.12-slim AS runtime
RUN useradd --system --create-home --uid 10001 app
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY app ./app
USER app
CMD ["python", "-m", "app.main"]
