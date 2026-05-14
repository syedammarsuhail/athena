# Multi-stage; final image runs as non-root, no shell layers
FROM python:3.12-slim AS builder
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim AS runtime
ARG USER_UID=10001
RUN useradd -u $USER_UID -m -s /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /install /usr/local
COPY serve.py train.py ./
USER app
EXPOSE 8080
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz').read()" || exit 1
CMD ["uvicorn", "serve:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
