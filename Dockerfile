# ----------------------------
# Stage 1: Build React Frontend
# ----------------------------
FROM node:22-alpine AS frontend

# Set working directory inside container
WORKDIR /ui

# Copy the frontend source code (React app)
COPY kommu-ui/ ./

# Install dependencies and build production assets
RUN npm install && npm run build


# ----------------------------
# Stage 2: Build Python Backend
# ----------------------------
FROM python:3.11-slim

# Set working directory for backend
WORKDIR /app

# Install OS dependencies (for building some Python libs)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY . .

# Copy the built frontend into backend's static directory
COPY --from=frontend /ui/dist /app/static/dashboard

# Create required directories
RUN mkdir -p /app/media /app/logs

# Expose backend port (match docker-compose.yml)
EXPOSE 8000

# Run FastAPI with Uvicorn
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
