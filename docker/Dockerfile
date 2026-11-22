# Evaluator image: the routing_pilot package, its tests and the task data.
# No model weights and no GPU. Models are served by the separate `ollama` service
# (see docker-compose.yml), reached over HTTP through OLLAMA_HOST.
#
#   docker build -f docker/Dockerfile -t routing-pilot .
#   docker run --rm routing-pilot                      # runs the test suite
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so code edits don't invalidate this layer.
COPY pyproject.toml README.md ./
RUN mkdir -p src/routing_pilot && touch src/routing_pilot/__init__.py \
    && pip install -e ".[dev]" \
    && rm -rf src

COPY src ./src
COPY tests ./tests
COPY configs ./configs
COPY data ./data
COPY scripts ./scripts
COPY results ./results

# Editable install: config.py locates configs/, data/ and results/ relative to the source tree.
RUN pip install --no-deps -e . \
    && useradd --create-home --uid 1000 app \
    && chown -R app:app /app
USER app

CMD ["python", "-m", "pytest", "-q"]
