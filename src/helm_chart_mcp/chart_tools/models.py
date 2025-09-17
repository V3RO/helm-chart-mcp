"""Data models for Helm chart dependency analysis."""

from typing import Dict, List, Optional, Any, Union
from enum import Enum
from pydantic import BaseModel, Field, ConfigDict


class ChartMetadata(BaseModel):
    """Represents Helm chart metadata from Chart.yaml."""

    api_version: str = Field(alias="apiVersion")
    name: str
    version: str
    description: Optional[str] = None
    type: Optional[str] = None
    keywords: Optional[List[str]] = None
    home: Optional[str] = None
    sources: Optional[List[str]] = None
    dependencies: Optional[List["ChartDependency"]] = None
    maintainers: Optional[List[Dict[str, str]]] = None
    icon: Optional[str] = None
    app_version: Optional[str] = Field(None, alias="appVersion")
    deprecated: Optional[bool] = None
    annotations: Optional[Dict[str, str]] = None

    model_config = ConfigDict(populate_by_name=True)


class ChartDependency(BaseModel):
    """Represents a Helm chart dependency."""

    name: str
    version: str
    repository: Optional[str] = None
    condition: Optional[str] = None
    tags: Optional[List[str]] = None
    enabled: Optional[bool] = None
    import_values: Optional[List[Union[str, Dict[str, Any]]]] = Field(
        None, alias="import-values"
    )
    alias: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class ValuesSchema(BaseModel):
    """Represents the schema of a values.yaml file."""

    properties: Dict[str, Any] = Field(default_factory=dict)
    required: List[str] = Field(default_factory=list)
    type: str = "object"
    description: Optional[str] = None
    examples: Optional[List[Dict[str, Any]]] = None


class ChartTemplate(BaseModel):
    """Represents a Helm chart template file."""

    name: str
    path: str
    content: str
    kind: Optional[str] = None


class ChartAnalysis(BaseModel):
    """Complete analysis of a Helm chart."""

    metadata: ChartMetadata
    values_schema: Optional[ValuesSchema] = None
    default_values: Optional[Dict[str, Any]] = None
    templates: List[ChartTemplate] = Field(default_factory=list)
    readme: Optional[str] = None
    notes: Optional[str] = None
    chart_path: Optional[str] = None
    is_dependency: bool = False


class DependencyAnalysis(BaseModel):
    """Analysis of chart dependencies from a parent chart."""

    parent_chart: str
    dependencies: List[ChartAnalysis] = Field(default_factory=list)
    dependency_values: Dict[str, Any] = Field(default_factory=dict)
    analysis_timestamp: Optional[str] = None


# MCP Response Models - Unified structures for all MCP tool responses


class ErrorType(str, Enum):
    """Standard error types for MCP responses."""

    DEPENDENCY_NOT_FOUND = "dependency_not_found"
    ANALYSIS_FAILED = "analysis_failed"
    DOWNLOAD_FAILED = "download_failed"
    VALIDATION_FAILED = "validation_failed"
    NO_DEFAULT_VALUES = "no_default_values"


class IssueSeverity(str, Enum):
    """Severity levels for validation issues."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class BaseResponse(BaseModel):
    """Base response model for all MCP tool responses."""

    success: bool
    message: str
    error: Optional[ErrorType] = None
    details: Optional[str] = None
    action_required: Optional[str] = None


class DependencyInfo(BaseModel):
    """Basic dependency information used across multiple responses."""

    name: str
    success: bool
    version: Optional[str] = None
    description: Optional[str] = None
    error: Optional[ErrorType] = None
    message: Optional[str] = None


class ValidationIssue(BaseModel):
    """Structured validation issue information."""

    type: str
    path: str
    message: str
    severity: IssueSeverity
    expected_type: Optional[str] = None
    actual_type: Optional[str] = None


class ValidationSummary(BaseModel):
    """Summary of validation results."""

    total_issues: int
    errors: int
    warnings: int


class DownloadResponse(BaseResponse):
    """Response model for download_dependencies tool."""

    dependencies_downloaded: int = 0
    cached_dependencies: Dict[str, str] = Field(default_factory=dict)


class DependencyAnalysisInfo(BaseModel):
    """Detailed dependency analysis information."""

    name: str
    success: bool
    version: Optional[str] = None
    description: Optional[str] = None
    templates_count: int = 0
    configuration_options_count: int = 0
    readme_available: bool = False
    kubernetes_resource_types: List[str] = Field(default_factory=list)
    error: Optional[ErrorType] = None
    message: Optional[str] = None


class AnalyzeResponse(BaseResponse):
    """Response model for analyze_dependencies tool."""

    cached_dependencies_count: int = 0
    dependencies: List[DependencyAnalysisInfo] = Field(default_factory=list)


class ConfigurationResponse(BaseResponse):
    """Response model for get_configuration_options tool."""

    dependency_name: Optional[str] = None
    version: Optional[str] = None
    description: Optional[str] = None
    values_schema: Optional[Dict[str, Any]] = None
    default_values: Optional[Dict[str, Any]] = None
    kubernetes_resources: List[str] = Field(default_factory=list)
    configuration_structure: Dict[str, str] = Field(default_factory=dict)


class ValidationResponse(BaseResponse):
    """Response model for validate_values tool."""

    dependency_name: Optional[str] = None
    version: Optional[str] = None
    validation_method: str = "default_values_structure_comparison"
    has_default_values: bool = False
    validation_passed: bool = False
    issues: List[ValidationIssue] = Field(default_factory=list)
    summary: Optional[ValidationSummary] = None
