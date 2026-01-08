"""
Logging middleware for PowerMem API
"""

import logging
import os
import sys
import json
import time
import uuid
from logging.handlers import RotatingFileHandler
from typing import Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from ..config import config
from ..utils.metrics import get_metrics_collector
from ..models.errors import APIError

# Setup logger
logger = logging.getLogger("server")


def setup_logging():
    """Setup logging configuration
    
    This function can be safely called multiple times.
    It will reconfigure loggers if called again.
    """
    log_level = getattr(logging, config.log_level.upper(), logging.INFO)
    
    # Create formatter
    if config.log_format == "json":
        formatter = JsonFormatter()
        text_formatter = None  # JSON format doesn't need text formatter
    else:
        # Improved text format with timestamp
        # Use right-aligned 7-character width for level name to accommodate WARNING/CRITICAL
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)7s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        text_formatter = formatter
    
    # Setup file handler if log_file is configured
    file_handler = None
    if config.log_file:
        try:
            # Create log file directory if it doesn't exist
            log_file_path = os.path.abspath(config.log_file)
            log_dir = os.path.dirname(log_file_path)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)
            
            # Use RotatingFileHandler with append mode to preserve history
            # Max file size: 10MB, keep 5 backup files
            file_handler = RotatingFileHandler(
                log_file_path,
                mode='a',  # Append mode to preserve history
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=5,
                encoding='utf-8'
            )
            file_handler.setLevel(log_level)
            if config.log_format == "json":
                file_handler.setFormatter(JsonFormatter())
            else:
                file_handler.setFormatter(text_formatter)
        except Exception as e:
            # If file logging fails, log to stderr and continue with console logging only
            print(f"Warning: Failed to setup file logging: {e}", file=sys.stderr)
            file_handler = None
    
    # Configure Uvicorn loggers FIRST (before they start logging)
    # This ensures the initial startup messages have timestamps
    uvicorn_loggers = [
        logging.getLogger("uvicorn"),
        logging.getLogger("uvicorn.error"),
        logging.getLogger("uvicorn.access"),
    ]
    
    for uvicorn_logger in uvicorn_loggers:
        uvicorn_logger.setLevel(log_level)
        # Remove existing handlers to avoid duplicates
        uvicorn_logger.handlers.clear()
        
        # Create console handler for uvicorn
        uvicorn_console_handler = logging.StreamHandler(sys.stdout)
        if config.log_format == "json":
            uvicorn_console_handler.setFormatter(JsonFormatter())
        else:
            # Use the same text formatter for consistency
            uvicorn_console_handler.setFormatter(text_formatter)
        uvicorn_logger.addHandler(uvicorn_console_handler)
        
        # Add file handler if configured
        if file_handler:
            # Create a new file handler for each logger (they share the same file)
            uvicorn_file_handler = RotatingFileHandler(
                os.path.abspath(config.log_file),
                mode='a',
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding='utf-8'
            )
            uvicorn_file_handler.setLevel(log_level)
            if config.log_format == "json":
                uvicorn_file_handler.setFormatter(JsonFormatter())
            else:
                uvicorn_file_handler.setFormatter(text_formatter)
            uvicorn_logger.addHandler(uvicorn_file_handler)
        
        uvicorn_logger.propagate = False
    
    # Setup handler for application logger
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    
    # Configure application logger
    logger.setLevel(log_level)
    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()
    logger.addHandler(console_handler)
    
    # Add file handler if configured
    if file_handler:
        logger.addHandler(file_handler)
    
    # Prevent duplicate logs
    logger.propagate = False


class JsonFormatter(logging.Formatter):
    """JSON formatter for structured logging"""
    
    def __init__(self, datefmt=None):
        super().__init__(datefmt=datefmt or "%Y-%m-%d %H:%M:%S")
    
    def format(self, record):
        log_data = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Add extra fields
        if hasattr(record, "request_id"):
            log_data["request_id"] = record.request_id
        if hasattr(record, "user_id"):
            log_data["user_id"] = record.user_id
        if hasattr(record, "agent_id"):
            log_data["agent_id"] = record.agent_id
        if hasattr(record, "method"):
            log_data["method"] = record.method
        if hasattr(record, "path"):
            log_data["path"] = record.path
        if hasattr(record, "status_code"):
            log_data["status_code"] = record.status_code
        if hasattr(record, "duration_ms"):
            log_data["duration_ms"] = round(record.duration_ms, 2)
        if hasattr(record, "client"):
            log_data["client"] = record.client
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        return json.dumps(log_data, ensure_ascii=False)


class LoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for request/response logging"""
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Generate request ID
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        
        # Start time
        start_time = time.time()
        
        # Log request
        logger.info(
            f"{request.method} {request.url.path}",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "client": request.client.host if request.client else None,
            }
        )
        
        try:
            # Process request
            response = await call_next(request)
            
            # Calculate duration
            duration = time.time() - start_time
            
            # Record metrics
            metrics_collector = get_metrics_collector()
            # Normalize path to endpoint
            endpoint = metrics_collector.normalize_endpoint(request.url.path)
            metrics_collector.record_api_request(
                method=request.method,
                endpoint=endpoint,
                status_code=response.status_code,
                duration=duration
            )
            
            # Log response
            logger.info(
                f"{request.method} {request.url.path} - {response.status_code}",
                extra={
                    "request_id": request_id,
                    "status_code": response.status_code,
                    "duration_ms": duration * 1000,
                }
            )
            
            # Add request ID to response header
            response.headers["X-Request-ID"] = request_id
            
            return response
            
        except Exception as e:
            duration = time.time() - start_time
            
            # Determine status code and whether this is an expected error
            status_code = 500
            is_expected_error = False
            
            if isinstance(e, APIError):
                status_code = e.status_code
                # Client errors (4xx) are expected, server errors (5xx) are unexpected
                is_expected_error = status_code < 500
            
            # Record metrics for error
            metrics_collector = get_metrics_collector()
            endpoint = metrics_collector.normalize_endpoint(request.url.path)
            metrics_collector.record_api_request(
                method=request.method,
                endpoint=endpoint,
                status_code=status_code,
                duration=duration
            )
            
            # For expected errors (4xx), log without stack trace
            # For unexpected errors (5xx), log with full stack trace
            if is_expected_error:
                logger.warning(
                    f"{request.method} {request.url.path} - {status_code}: {str(e)}",
                    extra={
                        "request_id": request_id,
                        "status_code": status_code,
                        "error": str(e),
                        "duration_ms": duration * 1000,
                    },
                )
            else:
                logger.error(
                    f"Error processing {request.method} {request.url.path}",
                    extra={
                        "request_id": request_id,
                        "status_code": status_code,
                        "error": str(e),
                        "duration_ms": duration * 1000,
                    },
                    exc_info=True,
                )
            raise


def log_request(request: Request, message: str, **kwargs):
    """
    Log a request with additional context.
    
    Args:
        request: FastAPI request object
        message: Log message
        **kwargs: Additional context
    """
    extra = {
        "request_id": getattr(request.state, "request_id", None),
        **kwargs
    }
    logger.info(message, extra=extra)
