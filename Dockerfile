FROM denoland/deno:bin-2.3.7 AS deno
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=deno /deno /usr/local/bin/deno
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV PORT=8080 PYTHONUNBUFFERED=1
CMD ["sh", "-c", "uvicorn app.compat8:app --host 0.0.0.0 --port ${PORT}"]