FROM node:22-alpine AS frontend
WORKDIR /ui
COPY kommu-ui/ ./
RUN npm install && npm run build


FROM python:3.11-slim AS kai
WORKDIR /app


RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*


COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install gunicorn uvicorn

COPY . .

COPY --from=frontend /ui/dist ./static
RUN mkdir -p /app/data /app/logs /app/media && chmod -R 777 /app/data /app/logs /app/media
EXPOSE 6090
CMD ["gunicorn", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "app:app", "--bind", "0.0.0.0:6090"]
