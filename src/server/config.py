"""
Configuration management for PowerMem API Server
"""

import os
from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import Field, ConfigDict, field_validator, model_validator


class ServerConfig(BaseSettings):
    """Server configuration settings"""
    
    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Ignore extra fields from .env that are not in this model
    )
    
    # Server settings
    host: str = Field(default="0.0.0.0", env="POWERMEM_SERVER_HOST")
    port: int = Field(default=8000, env="POWERMEM_SERVER_PORT")
    workers: int = Field(default=4, env="POWERMEM_SERVER_WORKERS")
    reload: bool = Field(default=False, env="POWERMEM_SERVER_RELOAD")
    
    # Authentication settings
    auth_enabled: bool = Field(default=True, env="POWERMEM_SERVER_AUTH_ENABLED")
    api_keys: str = Field(default="", env="POWERMEM_SERVER_API_KEYS")
    
    @model_validator(mode='after')
    def parse_auth_enabled_from_env(self):
        """Parse auth_enabled from environment variable, handling string 'false'"""
        # Read directly from environment to bypass Pydantic's bool parsing
        env_value = os.getenv('POWERMEM_SERVER_AUTH_ENABLED', '').strip().lower()
        if env_value:
            # Only update if explicitly set in environment
            self.auth_enabled = env_value in ('true', '1', 'yes', 'on', 'enabled')
        return self
    
    # Rate limiting settings
    rate_limit_enabled: bool = Field(default=True, env="POWERMEM_SERVER_RATE_LIMIT_ENABLED")
    rate_limit_per_minute: int = Field(default=100, env="POWERMEM_SERVER_RATE_LIMIT_PER_MINUTE")
    
    # Logging settings
    log_level: str = Field(default="INFO", env="POWERMEM_SERVER_LOG_LEVEL")
    log_format: str = Field(default="json", env="POWERMEM_SERVER_LOG_FORMAT")  # json or text
    log_file: Optional[str] = Field(default="server.log", env="POWERMEM_SERVER_LOG_FILE")  # Log file path, None to disable file logging
    
    # API settings
    api_title: str = Field(default="PowerMem API", env="POWERMEM_SERVER_API_TITLE")
    api_version: str = Field(default="v1", env="POWERMEM_SERVER_API_VERSION")
    api_description: str = Field(
        default="PowerMem HTTP API Server - Intelligent Memory System",
        env="POWERMEM_SERVER_API_DESCRIPTION"
    )
    
    # CORS settings
    cors_enabled: bool = Field(default=True, env="POWERMEM_SERVER_CORS_ENABLED")
    cors_origins: str = Field(default="*", env="POWERMEM_SERVER_CORS_ORIGINS")
    
    def get_api_keys_list(self) -> List[str]:
        """Get list of valid API keys"""
        if not self.api_keys:
            return []
        return [key.strip() for key in self.api_keys.split(",") if key.strip()]
    
    def get_cors_origins_list(self) -> List[str]:
        """Get list of CORS origins"""
        if self.cors_origins == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


# Global config instance
config = ServerConfig()

# Manually override config values from .env file if set
# This handles the case where Pydantic doesn't properly parse certain values
try:
    env_file_path = os.path.join(os.getcwd(), '.env')
    if os.path.exists(env_file_path):
        with open(env_file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    key_upper = key.upper()
                    
                    # Handle server settings
                    if key_upper == 'POWERMEM_SERVER_HOST':
                        config.host = value
                    elif key_upper == 'POWERMEM_SERVER_PORT':
                        try:
                            config.port = int(value)
                        except ValueError:
                            pass
                    elif key_upper == 'POWERMEM_SERVER_WORKERS':
                        try:
                            config.workers = int(value)
                        except ValueError:
                            pass
                    elif key_upper == 'POWERMEM_SERVER_RELOAD':
                        config.reload = value.lower() in ('true', '1', 'yes', 'on', 'enabled')
                    # Handle auth_enabled
                    elif key_upper == 'POWERMEM_SERVER_AUTH_ENABLED':
                        config.auth_enabled = value.lower() in ('true', '1', 'yes', 'on', 'enabled')
                    # Handle api_keys
                    elif key_upper == 'POWERMEM_SERVER_API_KEYS':
                        config.api_keys = value
                    # Handle rate limiting settings
                    elif key_upper == 'POWERMEM_SERVER_RATE_LIMIT_ENABLED':
                        config.rate_limit_enabled = value.lower() in ('true', '1', 'yes', 'on', 'enabled')
                    elif key_upper == 'POWERMEM_SERVER_RATE_LIMIT_PER_MINUTE':
                        try:
                            config.rate_limit_per_minute = int(value)
                        except ValueError:
                            pass
                    # Handle log_format
                    elif key_upper == 'POWERMEM_SERVER_LOG_FORMAT':
                        config.log_format = value.lower()
                    # Handle log_level
                    elif key_upper == 'POWERMEM_SERVER_LOG_LEVEL':
                        config.log_level = value.upper()
                    # Handle log_file
                    elif key_upper == 'POWERMEM_SERVER_LOG_FILE':
                        config.log_file = value if value else None
                    # Handle API settings
                    elif key_upper == 'POWERMEM_SERVER_API_TITLE':
                        config.api_title = value
                    elif key_upper == 'POWERMEM_SERVER_API_VERSION':
                        config.api_version = value
                    elif key_upper == 'POWERMEM_SERVER_API_DESCRIPTION':
                        config.api_description = value
                    # Handle CORS settings
                    elif key_upper == 'POWERMEM_SERVER_CORS_ENABLED':
                        config.cors_enabled = value.lower() in ('true', '1', 'yes', 'on', 'enabled')
                    elif key_upper == 'POWERMEM_SERVER_CORS_ORIGINS':
                        config.cors_origins = value
except Exception:
    # Fallback to environment variables
    # Server settings
    _host_env = os.getenv('POWERMEM_SERVER_HOST', '').strip()
    if _host_env:
        config.host = _host_env
    
    _port_env = os.getenv('POWERMEM_SERVER_PORT', '').strip()
    if _port_env:
        try:
            config.port = int(_port_env)
        except ValueError:
            pass
    
    _workers_env = os.getenv('POWERMEM_SERVER_WORKERS', '').strip()
    if _workers_env:
        try:
            config.workers = int(_workers_env)
        except ValueError:
            pass
    
    _reload_env = os.getenv('POWERMEM_SERVER_RELOAD', '').strip().lower()
    if _reload_env:
        config.reload = _reload_env in ('true', '1', 'yes', 'on', 'enabled')
    
    # Authentication settings
    _auth_env = os.getenv('POWERMEM_SERVER_AUTH_ENABLED', '').strip().lower()
    if _auth_env:
        config.auth_enabled = _auth_env in ('true', '1', 'yes', 'on', 'enabled')
    
    _api_keys_env = os.getenv('POWERMEM_SERVER_API_KEYS', '').strip()
    if _api_keys_env:
        config.api_keys = _api_keys_env
    
    # Rate limiting settings
    _rate_limit_enabled_env = os.getenv('POWERMEM_SERVER_RATE_LIMIT_ENABLED', '').strip().lower()
    if _rate_limit_enabled_env:
        config.rate_limit_enabled = _rate_limit_enabled_env in ('true', '1', 'yes', 'on', 'enabled')
    
    _rate_limit_per_minute_env = os.getenv('POWERMEM_SERVER_RATE_LIMIT_PER_MINUTE', '').strip()
    if _rate_limit_per_minute_env:
        try:
            config.rate_limit_per_minute = int(_rate_limit_per_minute_env)
        except ValueError:
            pass
    
    # Logging settings
    _log_format_env = os.getenv('POWERMEM_SERVER_LOG_FORMAT', '').strip().lower()
    if _log_format_env:
        config.log_format = _log_format_env
    
    _log_level_env = os.getenv('POWERMEM_SERVER_LOG_LEVEL', '').strip().upper()
    if _log_level_env:
        config.log_level = _log_level_env
    
    _log_file_env = os.getenv('POWERMEM_SERVER_LOG_FILE', '').strip()
    if _log_file_env:
        config.log_file = _log_file_env if _log_file_env else None
    
    # API settings
    _api_title_env = os.getenv('POWERMEM_SERVER_API_TITLE', '').strip()
    if _api_title_env:
        config.api_title = _api_title_env
    
    _api_version_env = os.getenv('POWERMEM_SERVER_API_VERSION', '').strip()
    if _api_version_env:
        config.api_version = _api_version_env
    
    _api_description_env = os.getenv('POWERMEM_SERVER_API_DESCRIPTION', '').strip()
    if _api_description_env:
        config.api_description = _api_description_env
    
    # CORS settings
    _cors_enabled_env = os.getenv('POWERMEM_SERVER_CORS_ENABLED', '').strip().lower()
    if _cors_enabled_env:
        config.cors_enabled = _cors_enabled_env in ('true', '1', 'yes', 'on', 'enabled')
    
    _cors_origins_env = os.getenv('POWERMEM_SERVER_CORS_ORIGINS', '').strip()
    if _cors_origins_env:
        config.cors_origins = _cors_origins_env
