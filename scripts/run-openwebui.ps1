#requires -Version 5.1
<#
.SYNOPSIS
    Run Open WebUI in Docker, pointed at the chatbot's OpenAI-compatible shim,
    with the login prompt disabled (single-user mode).

.DESCRIPTION
    The data volume is REUSED by default, so the one-time DB migrations and model
    download happen only on the first boot. WEBUI_AUTH=False never creates a login
    account, so a reused volume stays login-free. Pass -Fresh to wipe the volume —
    needed the first time, or to disable auth on a volume that already has accounts
    (the flag is ignored once an account exists).

    The chatbot service must already be running (python main_api.py) and bound to
    an interface the container can reach: set api_host="0.0.0.0" in main_api.py
    and API_AUTH_TOKEN in .env, since the port is then non-local.

.EXAMPLE
    ./scripts/run-openwebui.ps1 -Fresh
    First run: clean, no-login Open WebUI on http://localhost:3000.

.EXAMPLE
    ./scripts/run-openwebui.ps1
    Restart reusing cached data (no re-migrate, no re-download).
#>
[CmdletBinding()]
param(
    # Host port Open WebUI is published on.
    [int]$Port = 3000,
    # The chatbot shim base URL as seen *from inside the container*.
    [string]$AppUrl = "http://host.docker.internal:8000/v1",
    # Bearer token for the shim. Falls back to $env:API_AUTH_TOKEN, then .env,
    # then "sk-noauth" (Open WebUI rejects an empty key even when auth is off).
    [string]$ApiKey,
    [string]$Container = "open-webui",
    [string]$Volume = "open-webui",
    [string]$Image = "ghcr.io/open-webui/open-webui:main",
    # Wipe the data volume for a clean, no-login reset. Use it the first time, or
    # to disable auth on a volume that already has accounts. Without it the volume
    # is reused, so migrations and model downloads happen only once.
    [switch]$Fresh
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "docker not found on PATH. Install Docker Desktop and retry."
}

# Resolve the API key: explicit param > env > .env > placeholder.
if (-not $ApiKey) { $ApiKey = $env:API_AUTH_TOKEN }
if (-not $ApiKey) {
    $envFile = Join-Path $PSScriptRoot "..\.env"
    if (Test-Path $envFile) {
        $line = Select-String -Path $envFile -Pattern '^\s*API_AUTH_TOKEN\s*=' -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($line) {
            $ApiKey = ($line.Line -replace '^\s*API_AUTH_TOKEN\s*=', '').Trim().Trim('"').Trim("'")
        }
    }
}
if (-not $ApiKey) {
    $ApiKey = "sk-noauth"
    Write-Warning "No API_AUTH_TOKEN found; using placeholder key '$ApiKey'. The shim is unauthenticated."
}

# Remove any prior container.
$existing = docker ps -aq -f "name=^$Container$"
if ($existing) {
    Write-Host "Removing existing container '$Container'..."
    docker rm -f $Container | Out-Null
}

# Reuse the volume by default (caches migrations + model download, stays login-free
# because WEBUI_AUTH=False never makes an account). -Fresh wipes it for a clean reset.
if ($Fresh) {
    $vol = docker volume ls -q -f "name=^$Volume$"
    if ($vol) {
        Write-Host "Removing data volume '$Volume' for a clean reset..."
        docker volume rm $Volume | Out-Null
    }
} else {
    Write-Host "Reusing data volume '$Volume' (pass -Fresh to reset; use it on the first run)."
}

# Strip Open WebUI down to a plain chat frontend:
#  - WEBUI_AUTH=False                  no login
#  - ENABLE_OPENAI_API / OLLAMA_API    talk only to the chatbot shim, never probe Ollama
#  - ENABLE_*_GENERATION=False         no hidden title/tag/follow-up/autocomplete/query
#                                      calls (those fire extra requests at the backend and
#                                      would pollute the chatbot's session memory)
#  - the rest disable RAG/web/image/code/community/arena features and the update + telemetry pings
Write-Host "Starting Open WebUI -> $AppUrl (login disabled, chat-only)..."
docker run -d -p "${Port}:8080" `
    --add-host=host.docker.internal:host-gateway `
    -e WEBUI_AUTH=False `
    -e ENABLE_PERSISTENT_CONFIG=False `
    -e ENABLE_OPENAI_API=True `
    -e ENABLE_OLLAMA_API=False `
    -e ENABLE_TITLE_GENERATION=False `
    -e ENABLE_TAGS_GENERATION=False `
    -e ENABLE_FOLLOW_UP_GENERATION=False `
    -e ENABLE_AUTOCOMPLETE_GENERATION=False `
    -e ENABLE_RETRIEVAL_QUERY_GENERATION=False `
    -e ENABLE_SEARCH_QUERY_GENERATION=False `
    -e ENABLE_WEB_SEARCH=False `
    -e ENABLE_IMAGE_GENERATION=False `
    -e ENABLE_CODE_INTERPRETER=False `
    -e ENABLE_CODE_EXECUTION=False `
    -e ENABLE_COMMUNITY_SHARING=False `
    -e ENABLE_EVALUATION_ARENA_MODELS=False `
    -e ENABLE_VERSION_UPDATE_CHECK=False `
    -e RAG_EMBEDDING_ENGINE=openai `
    -e ANONYMIZED_TELEMETRY=False `
    -e DO_NOT_TRACK=True `
    -e SCARF_NO_ANALYTICS=True `
    -e OPENAI_API_BASE_URL=$AppUrl `
    -e OPENAI_API_KEY=$ApiKey `
    -v "${Volume}:/app/backend/data" `
    --name $Container `
    $Image | Out-Null

Write-Host ""
Write-Host "Open WebUI is starting. Browse to http://localhost:$Port (no login)." -ForegroundColor Green
Write-Host "Pick the 'chatbot' model — auto-discovered via /v1/models."
Write-Host "Logs:  docker logs -f $Container"
Write-Host "Stop:  docker rm -f $Container"
