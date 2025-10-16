FROM node:22-alpine AS frontend

WORKDIR /ui


COPY kommu-ui/ ./


RUN npm install && npm run build

FROM python:3.11-slim AS backend

WORKDIR /app


RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libpq-dev \
    git \
    && rm -rf /var/lib/apt/lists/*


RUN mkdir -p /app/data /app/logs && chmod -R 777 /app/data /app/logs


COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .


COPY --from=frontend /ui/dist /app/static

EXPOSE 6090


CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "6090"]

