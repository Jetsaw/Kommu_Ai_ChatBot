FROM python:3.11-slim

WORKDIR /app
ENV PIP_NO_CACHE_DIR=1


RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt


COPY . .

# startup helper
RUN chmod +x entrypoint.sh

EXPOSE 8000
ENV PORT=8000
CMD ["./entrypoint.sh"]
