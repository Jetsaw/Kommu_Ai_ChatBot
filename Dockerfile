
FROM node:22-alpine AS frontend-builder
WORKDIR /app/kommu-ui
COPY kommu-ui/package*.json ./
RUN npm install
COPY kommu-ui/ .
RUN npm run build


FROM python:3.11-slim AS backend
WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


COPY . .

COPY --from=frontend-builder /app/kommu-ui/dist /app/kommu-ui/dist

RUN mkdir -p logs data media
EXPOSE 6090
CMD ["gunicorn", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "app:app", "--bind", "0.0.0.0:6090"]
