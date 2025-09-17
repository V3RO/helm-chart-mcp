"""Configuration models for Helm Chart MCP Server."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class OciRegistryConfig(BaseModel):
    """OCI registry configuration."""

    username: Optional[str] = Field(None, description="OCI registry username")
    password: Optional[str] = Field(None, description="OCI registry password")
    registry: Optional[str] = Field(None, description="Default OCI registry")


class CacheConfig(BaseModel):
    """Cache configuration."""

    directory: Path = Field(
        default_factory=lambda: Path.home() / ".helm" / "cache" / "dependency",
        description="Cache directory for downloaded charts",
    )
    metadata_filename: str = Field(
        default="cache_metadata.json", description="Filename for cache metadata"
    )


class SchemaTypes(str, Enum):
    """JSON Schema type constants."""

    OBJECT = "object"
    ARRAY = "array"
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    NULL = "null"


class McpResourceUris(str, Enum):
    """MCP resource URI constants."""

    SCHEMA_URI = "dependency://schema"
    SUMMARY = "dependency://summary"
    VALUES = "dependency://values"
    TEMPLATES = "dependency://templates"


class McpToolNames(str, Enum):
    """MCP tool name constants."""

    DOWNLOAD_DEPENDENCIES = "download_dependencies"
    ANALYZE_DEPENDENCIES = "analyze_chart_dependencies"
    GET_CONFIG_OPTIONS = "get_configuration_options"
    VALIDATE_VALUES = "validate_dependency_values"


class HttpConfig(BaseModel):
    """HTTP configuration."""

    timeout: int = Field(default=30, ge=1, description="HTTP timeout in seconds")
    max_retries: int = Field(default=3, ge=0, description="Maximum retry attempts")
    retry_delay: int = Field(default=1, ge=0, description="Retry delay in seconds")


class HelmChartMcpSettings(BaseSettings):
    """Main configuration settings for Helm Chart MCP Server."""

    model_config = SettingsConfigDict(
        env_prefix="HELM_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Logging
    log_level: str = Field(default="INFO", description="Logging level")

    # OCI Registry settings
    oci_username: Optional[str] = Field(None, description="OCI registry username")
    oci_password: Optional[str] = Field(None, description="OCI registry password")
    oci_registry: Optional[str] = Field(None, description="Default OCI registry")

    # Cache settings
    chart_cache_dir: Optional[Path] = Field(
        None, description="Chart cache directory override"
    )

    # HTTP settings
    http_timeout: int = Field(default=30, ge=1, description="HTTP timeout in seconds")
    max_retries: int = Field(default=3, ge=0, description="Maximum retry attempts")
    retry_delay: int = Field(default=1, ge=0, description="Retry delay in seconds")

    @property
    def oci_config(self) -> OciRegistryConfig:
        """Get OCI registry configuration."""
        return OciRegistryConfig(
            username=self.oci_username,
            password=self.oci_password,
            registry=self.oci_registry,
        )

    @property
    def cache_config(self) -> CacheConfig:
        """Get cache configuration."""
        cache_dir = (
            self.chart_cache_dir or Path.home() / ".helm" / "cache" / "dependency"
        )
        return CacheConfig(directory=cache_dir)

    @property
    def http_config(self) -> HttpConfig:
        """Get HTTP configuration."""
        return HttpConfig(
            timeout=self.http_timeout,
            max_retries=self.max_retries,
            retry_delay=self.retry_delay,
        )


settings = HelmChartMcpSettings()
