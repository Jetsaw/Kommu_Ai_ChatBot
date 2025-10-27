FROM node:22-slim AS frontend-builder
WORKDIR /app/kommu-ui
COPY kommu-ui/package*.json ./
ARG FRONTEND_PYTHON_PACKAGES=""
RUN if [ -n "$FRONTEND_PYTHON_PACKAGES" ]; then \
      if command -v apt-get >/dev/null 2>&1; then \
        apt-get update && \
        apt-get install -y --no-install-recommends python3 python3-pip && \
        rm -rf /var/lib/apt/lists/*; \
      elif command -v apk >/dev/null 2>&1; then \
        apk add --no-cache python3 py3-pip; \
      else \
        echo "Unable to install python3-pip in this image" >&2 && exit 1; \
      fi && \
      python3 -m pip install --no-cache-dir $FRONTEND_PYTHON_PACKAGES; \
    fi
RUN npm ci --include=dev
COPY kommu-ui/ .
RUN if [ -f vite.config.js ]; then mv vite.config.js vite.config.mjs; fi
ENV NODE_OPTIONS="--experimental-vm-modules"
ENV NODE_ENV=production
RUN npm run build



FROM python:3.11-slim AS backend
WORKDIR /app

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