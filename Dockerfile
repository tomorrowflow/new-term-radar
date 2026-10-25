FROM python:3.12-slim

# The radar needs only the standard library; markdown is for --mail.
RUN pip install --no-cache-dir "markdown>=3.5" \
 && useradd --create-home --uid 1000 radar \
 && mkdir /data && chown radar /data

WORKDIR /app
COPY radar/ radar/
COPY config.example.json .

USER radar
ENV RADAR_CONFIG=/data/config.json \
    RADAR_STATE=/data/state \
    RADAR_HTTP_PORT=8787 \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8787
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/health')"
CMD ["python", "-m", "radar.http_api"]
