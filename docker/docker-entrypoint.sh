#!/bin/bash
set -e

# Function to print log messages
log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*"
}

# Function to validate required environment variables
validate_config() {
    log "Validating configuration..."
    
    # Check if API keys are set when auth is enabled
    if [ "${POWERMEM_SERVER_AUTH_ENABLED:-true}" = "true" ] && [ -z "${POWERMEM_SERVER_API_KEYS}" ]; then
        log "Warning: POWERMEM_SERVER_AUTH_ENABLED is true but POWERMEM_SERVER_API_KEYS is not set"
        log "Server will start but API authentication may fail"
    fi
    
    log "Configuration validation completed"
}

# Main execution
main() {
    log "Starting PowerMem Server..."
    
    # Change to app directory to ensure .env file can be found
    cd /app || exit 1
    
    # Check if .env file exists (mounted or copied)
    if [ -f "/app/.env" ]; then
        log ".env file found at /app/.env"
    elif [ -f "/app/../.env" ]; then
        log ".env file found at parent directory"
    else
        log "No .env file found, using environment variables only"
    fi
    
    # Validate configuration
    validate_config
    
    # Log configuration (without sensitive data)
    log "Server Configuration:"
    log "  Host: ${POWERMEM_SERVER_HOST:-0.0.0.0}"
    log "  Port: ${POWERMEM_SERVER_PORT:-8000}"
    log "  Workers: ${POWERMEM_SERVER_WORKERS:-4}"
    log "  Log Level: ${POWERMEM_SERVER_LOG_LEVEL:-INFO}"
    log "  Auth Enabled: ${POWERMEM_SERVER_AUTH_ENABLED:-true}"
    log "  CORS Enabled: ${POWERMEM_SERVER_CORS_ENABLED:-true}"
    
    # Execute the command
    log "Launching server..."
    exec "$@"
}

# Run main function
main "$@"

