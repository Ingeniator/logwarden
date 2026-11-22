# logwarden in a container: no Python needed on the host. Weaver and the OTel semantic
# conventions are downloaded at build time (SHA-256 checked), so checks run offline.
#   docker run --rm -v "$PWD:/work" ghcr.io/ingeniator/logwarden check logs.jsonl
#   docker run --rm -p 4318:4318 -v "$PWD:/work" ghcr.io/ingeniator/logwarden capture --host 0.0.0.0
FROM python:3.13-slim

LABEL org.opencontainers.image.source="https://github.com/Ingeniator/logwarden" \
      org.opencontainers.image.description="Guardrail for structured logs: checks JSON-lines and OTLP logs against the logwarden logging standard" \
      org.opencontainers.image.licenses="Apache-2.0"

# logwarden's own messages as JSON records that pass its standard: a container's output usually
# ends up in a log pipeline. LOGWARDEN_LOG_FORMAT=text for plain text.
ENV LOGWARDEN_CACHE=/opt/logwarden \
    LOGWARDEN_LOG_FORMAT=json \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY . /tmp/src
RUN pip install /tmp/src \
 && rm -rf /tmp/src \
 && logwarden setup \
 && chmod -R a+rX "$LOGWARDEN_CACHE" \
 && printf '%s\n' '{"timestamp":"2026-01-01T00:00:00Z","level":"info","event":"image_built","logger":"build","service.name":"logwarden","service.version":"0","deployment.environment.name":"test"}' \
    | logwarden check -

WORKDIR /work
EXPOSE 4318
ENTRYPOINT ["logwarden"]
CMD ["--help"]
