# Promulga - governance document workflow
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PROMULGA_DATA_DIR=/data \
    TZ=Europe/Brussels

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 promulga \
 && mkdir -p /data && chown promulga:promulga /data

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY promulga ./promulga
RUN pip install --no-cache-dir ".[postgres]"
COPY deploy/entrypoint.sh /usr/local/bin/promulga-entrypoint
RUN sed -i 's/\r$//' /usr/local/bin/promulga-entrypoint && chmod 755 /usr/local/bin/promulga-entrypoint

# Starts as root only to read the Docker secrets, then runs as "promulga" (see entrypoint.sh).
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"
ENTRYPOINT ["promulga-entrypoint"]
CMD ["promulga", "serve", "--host", "0.0.0.0", "--port", "8000"]
