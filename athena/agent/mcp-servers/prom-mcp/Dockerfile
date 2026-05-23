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
COPY server.py ./
USER app
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]


