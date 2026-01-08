"""
CLI command for starting the PowerMem API server
"""

import click
import uvicorn
from ..config import config
from ..middleware.logging import setup_logging


@click.command()
@click.option("--host", default=None, help="Host to bind to")
@click.option("--port", default=None, type=int, help="Port to bind to")
@click.option("--workers", default=None, type=int, help="Number of worker processes")
@click.option("--reload", is_flag=True, help="Enable auto-reload")
@click.option("--log-level", default=None, help="Log level")
def server(host, port, workers, reload, log_level):
    """
    Start the PowerMem API server.
    
    Example:
        powermem-server --host 0.0.0.0 --port 8000 --reload
    """
    # Override config with CLI options
    if host:
        config.host = host
    if port:
        config.port = port
    if workers:
        config.workers = workers
    if reload:
        config.reload = True
    if log_level:
        config.log_level = log_level
    
    # Debug: Print current log format (can be removed later)
    import sys
    print(f"[DEBUG] Current log_format: {config.log_format}", file=sys.stderr)
    
    # Setup logging BEFORE starting uvicorn to ensure all logs have timestamps
    setup_logging()
    
    # Start server
    uvicorn.run(
        "server.main:app",
        host=config.host,
        port=config.port,
        reload=config.reload,
        workers=config.workers if not config.reload else 1,
        log_level=config.log_level.lower(),
    )


if __name__ == "__main__":
    server()
