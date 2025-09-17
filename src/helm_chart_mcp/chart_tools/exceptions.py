"""Custom exceptions for Helm chart dependency MCP server."""


class HelmChartMCPError(Exception):
    """Base exception for Helm chart MCP server errors."""

    pass


class ChartNotFoundError(HelmChartMCPError):
    """Raised when a chart cannot be found."""

    pass


class ChartDownloadError(HelmChartMCPError):
    """Raised when chart download fails."""

    pass


class ChartAnalysisError(HelmChartMCPError):
    """Raised when chart analysis fails."""

    pass


class AuthenticationError(HelmChartMCPError):
    """Raised when OCI registry authentication fails."""

    pass


class InvalidChartError(HelmChartMCPError):
    """Raised when chart structure is invalid."""

    pass


class CacheError(HelmChartMCPError):
    """Raised when cache operations fail."""

    pass
