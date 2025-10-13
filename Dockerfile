# -------- frontend --------
FROM node:22-alpine AS frontend
WORKDIR /ui
COPY kommu-ui/ ./
RUN npm install && npm run build

# -------- Backend + FastAPI --------
FROM python:3.11-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc libpq-dev git && \
    rm -rf /var/lib/apt/lists/*

# Install backend dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY . .

# Copy built dashboard
COPY --from=frontend /ui/dist /app/static/dashboard

# Create persistent dirs
RUN mkdir -p /app/media /app/logs

# Internal FastAPI port
EXPOSE 8000

# Start FastAPI
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
