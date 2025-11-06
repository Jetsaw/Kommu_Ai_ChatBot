GNU nano 4.8                               Dockerfile                                          # ---------- FRONTEND BUILD ----------
FROM node:22-alpine AS frontend-builder
WORKDIR /app/kommu-ui

# Install dependencies for the UI
COPY kommu-ui/package*.json ./
RUN npm install && npm install -D tailwindcss postcss autoprefixer

# Copy full frontend source
COPY kommu-ui/ ./
RUN npm run build

# ---------- BACKEND BUILD ----------
FROM python:3.11-slim AS kai
WORKDIR /app

# Install required system packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl build-essential libgomp1 && \
    rm -rf /var/lib/apt/lists/*

# Copy backend files
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt


# Copy built frontend
COPY --from=frontend-builder /app/kommu-ui/dist /app/kommu-ui/dist

# Expose app port
EXPOSE 6090

# Start the FastAPI app with Gunicorn + Uvicorn worker
CMD ["gunicorn", "-w", "1", "-k", "uvicorn.workers.UvicornWorker", "app:app", "--bind", "0.0.0.0:6090"]








