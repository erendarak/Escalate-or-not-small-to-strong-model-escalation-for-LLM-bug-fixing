#!/usr/bin/env sh
# Pull the two study models into the running `ollama` container (about 1 GB + 4.7 GB).
set -eu
cd "$(dirname "$0")/.."
for m in qwen2.5-coder:1.5b qwen2.5-coder:7b; do
  docker compose exec ollama ollama pull "$m"
done
docker compose exec ollama ollama list
