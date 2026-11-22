# Pull the two study models into the running `ollama` container (about 1 GB + 4.7 GB).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
foreach ($m in @("qwen2.5-coder:1.5b", "qwen2.5-coder:7b")) {
    docker compose exec ollama ollama pull $m
    if ($LASTEXITCODE -ne 0) { throw "pull failed: $m" }
}
docker compose exec ollama ollama list
