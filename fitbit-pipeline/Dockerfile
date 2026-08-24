# Optional container. The pipeline runs fine without Docker; this exists for
# hosts where a virtualenv is inconvenient.
#
#   docker build -t fitbit-pipeline .
#   docker run --rm -v "$PWD/data:/app/data" -v "$PWD/secrets:/app/secrets" \
#              -v "$PWD/config.toml:/app/config.toml:ro" fitbit-pipeline daily
#
# The first authorization needs a browser, so run `fitbit-sync auth` on the host
# and mount the resulting secrets directory, or run the container with
# --network host and forward the loopback port.

FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY fitbit_pipeline ./fitbit_pipeline
RUN pip install --no-cache-dir .

# Data and secrets are mounted, never baked in.
VOLUME ["/app/data", "/app/secrets"]

ENV FITBIT_SYNC_CONFIG=/app/config.toml \
    FITBIT_SYNC_DATABASE_PATH=/app/data/fitbit.sqlite3 \
    FITBIT_SYNC_AUTH_TOKEN_FILE=/app/secrets/token.json \
    FITBIT_SYNC_AUTH_CLIENT_SECRET_FILE=/app/secrets/client_secret.json \
    FITBIT_SYNC_LOGGING_FORMAT=json

EXPOSE 8722

ENTRYPOINT ["fitbit-sync"]
CMD ["daily"]
