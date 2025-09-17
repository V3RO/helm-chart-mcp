"""MCP server for Helm chart dependency analysis."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List


import yaml
from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import Resource, Tool, TextContent

from .chart_tools.chart_analyzer import ChartDependencyAnalyzer
from .chart_tools.config import settings, McpResourceUris, McpToolNames
from .chart_tools.dependency_cache import DependencyCacheManager
from .chart_tools.models import (
    DependencyAnalysis,
    ErrorType,
    IssueSeverity,
    ValidationIssue,
    ValidationSummary,
    DownloadResponse,
    DependencyAnalysisInfo,
    AnalyzeResponse,
    ConfigurationResponse,
    ValidationResponse,
)


logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("helm-chart-dependency-mcp")


class HelmChartDependencyMCPServer:
    """MCP server for analyzing Helm chart dependencies."""

    def __init__(self) -> None:
        self.server = Server("helm-chart-dependency-mcp")
        self.analyzer = ChartDependencyAnalyzer()
        self.dependency_cache = DependencyCacheManager()
        self._analysis_cache: Dict[str, DependencyAnalysis] = {}

        # Register handlers
        self._register_handlers()

    def _register_handlers(self) -> None:
        """Register MCP server handlers."""

        @self.server.list_resources()
        async def handle_list_resources() -> List[Resource]:
            """List available resources."""
            return [
                Resource(
                    uri=McpResourceUris.SCHEMA_URI,
                    name="Dependency Chart Schemas",
                    description="JSON schemas for all dependency charts",
                    mimeType="application/json",
                ),
                Resource(
                    uri=McpResourceUris.SUMMARY,
                    name="Dependency Analysis Summary",
                    description="Summary of all analyzed dependency charts with their configurations",
                    mimeType="application/json",
                ),
                Resource(
                    uri=McpResourceUris.VALUES,
                    name="Dependency Values Configuration",
                    description="Current values configuration for dependency charts",
                    mimeType="application/yaml",
                ),
                Resource(
                    uri=McpResourceUris.TEMPLATES,
                    name="Dependency Templates Overview",
                    description="Overview of Kubernetes resources created by dependency charts",
                    mimeType="application/json",
                ),
            ]

        @self.server.read_resource()
        async def handle_read_resource(uri: str) -> str:
            """Read a specific resource."""
            if not self._analysis_cache:
                return json.dumps(
                    {
                        "error": "No chart analysis available. Please analyze a chart first using the analyze_chart_dependencies tool."
                    }
                )

            # Get the most recent analysis
            latest_analysis = list(self._analysis_cache.values())[-1]

            if uri == McpResourceUris.SCHEMA_URI:
                schemas = {}
                for dep in latest_analysis.dependencies:
                    if dep.values_schema:
                        schemas[dep.metadata.name] = {
                            "properties": dep.values_schema.properties,
                            "required": dep.values_schema.required,
                            "type": dep.values_schema.type,
                            "description": dep.values_schema.description,
                        }
                return json.dumps(schemas, indent=2)

            elif uri == McpResourceUris.SUMMARY:
                summary = self.analyzer.get_dependency_configuration_summary(
                    latest_analysis
                )
                return json.dumps(summary, indent=2)

            elif uri == McpResourceUris.VALUES:
                return yaml.dump(
                    latest_analysis.dependency_values, default_flow_style=False
                )

            elif uri == McpResourceUris.TEMPLATES:
                templates_info = {}
                for dep in latest_analysis.dependencies:
                    templates_info[dep.metadata.name] = [
                        {
                            "name": t.name,
                            "path": t.path,
                            "kind": t.kind,
                            "description": f"Kubernetes {t.kind} resource"
                            if t.kind
                            else "Kubernetes resource",
                        }
                        for t in dep.templates
                    ]
                return json.dumps(templates_info, indent=2)

            else:
                return json.dumps({"error": f"Unknown resource URI: {uri}"})

        @self.server.list_tools()
        async def handle_list_tools() -> List[Tool]:
            """List available tools."""
            return [
                Tool(
                    name=McpToolNames.DOWNLOAD_DEPENDENCIES,
                    description="Download and cache chart dependencies from Chart.yaml content. Returns structured JSON with download status, cached dependencies, and cache statistics. Supports multiple calls if Chart.yaml changes.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "chart_yaml_content": {
                                "type": "string",
                                "description": "Content of the Chart.yaml file containing dependency definitions",
                            },
                        },
                        "required": ["chart_yaml_content"],
                    },
                ),
                Tool(
                    name=McpToolNames.ANALYZE_DEPENDENCIES,
                    description="Analyze cached chart dependencies. Returns structured JSON with dependency analysis including versions, descriptions, Kubernetes resources, and configuration options count. Call download_dependencies first to cache the dependencies.",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                ),
                Tool(
                    name=McpToolNames.GET_CONFIG_OPTIONS,
                    description="Get configuration options for a specific dependency chart. Returns structured JSON with schema, default values, Kubernetes resources, and flattened configuration structure for easy AI consumption. Call download_dependencies first to cache the dependencies.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "dependency_name": {
                                "type": "string",
                                "description": "Name of the dependency chart to get configuration options for",
                            },
                        },
                        "required": ["dependency_name"],
                    },
                ),
                Tool(
                    name=McpToolNames.VALIDATE_VALUES,
                    description="Validate values configuration against dependency chart schema. Returns structured JSON with validation results, detailed issue analysis, and severity categorization. Call download_dependencies first to cache the dependencies.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "dependency_name": {
                                "type": "string",
                                "description": "Name of the dependency chart",
                            },
                            "values": {
                                "type": "object",
                                "description": "Values to validate against the dependency schema",
                            },
                        },
                        "required": ["dependency_name", "values"],
                    },
                ),
            ]

        @self.server.call_tool()
        async def handle_call_tool(
            name: str, arguments: Dict[str, Any]
        ) -> List[TextContent]:
            """Handle tool calls."""
            try:
                if name == McpToolNames.DOWNLOAD_DEPENDENCIES:
                    return await self._download_dependencies(arguments)
                elif name == McpToolNames.ANALYZE_DEPENDENCIES:
                    return await self._analyze_chart_dependencies(arguments)
                elif name == McpToolNames.GET_CONFIG_OPTIONS:
                    return await self._get_configuration_options(arguments)
                elif name == McpToolNames.VALIDATE_VALUES:
                    return await self._validate_dependency_values(arguments)
                else:
                    return [TextContent(type="text", text=f"Unknown tool: {name}")]

            except Exception as e:
                logger.error(f"Error in tool {name}: {e}")
                return [TextContent(type="text", text=f"Error: {str(e)}")]

    async def _download_dependencies(
        self, arguments: Dict[str, Any]
    ) -> List[TextContent]:
        """Download and cache dependencies from Chart.yaml content."""
        chart_yaml_content = arguments["chart_yaml_content"]

        logger.info("Starting dependency download and caching")

        try:
            # Download and cache dependencies
            cached_deps = await self.dependency_cache.download_and_cache_dependencies(
                chart_yaml_content
            )

            if not cached_deps:
                response = DownloadResponse(
                    success=False,
                    message="No dependencies found in Chart.yaml or all downloads failed",
                    dependencies_downloaded=0,
                    cached_dependencies={},
                )
                return [
                    TextContent(type="text", text=response.model_dump_json(indent=2))
                ]

            # Convert Path objects to strings for Pydantic model
            cached_deps_str = {name: str(path) for name, path in cached_deps.items()}

            response = DownloadResponse(
                success=True,
                message="Dependencies download completed successfully",
                dependencies_downloaded=len(cached_deps),
                cached_dependencies=cached_deps_str,
            )

            return [TextContent(type="text", text=response.model_dump_json(indent=2))]

        except Exception as e:
            logger.error(f"Error downloading dependencies: {e}")
            error_response = DownloadResponse(
                success=False,
                error=ErrorType.DOWNLOAD_FAILED,
                message="Error downloading dependencies",
                details=str(e),
            )
            return [
                TextContent(type="text", text=error_response.model_dump_json(indent=2))
            ]

    async def _analyze_chart_dependencies(
        self, arguments: Dict[str, Any]
    ) -> List[TextContent]:
        """Analyze cached chart dependencies."""
        logger.info("Starting cached chart dependency analysis")

        try:
            cached_deps = self.dependency_cache.list_cached_dependencies()

            if not cached_deps:
                response = AnalyzeResponse(
                    success=False,
                    message="No cached dependencies found",
                    action_required="run download_dependencies first",
                    cached_dependencies_count=0,
                    dependencies=[],
                )
                return [
                    TextContent(type="text", text=response.model_dump_json(indent=2))
                ]

            dependencies_analysis = []

            for dep_name in cached_deps:
                try:
                    dependency_path = self.dependency_cache.get_dependency_path(
                        dep_name
                    )
                    if dependency_path is None:
                        dependencies_analysis.append(
                            DependencyAnalysisInfo(
                                name=dep_name,
                                success=False,
                                error=ErrorType.DEPENDENCY_NOT_FOUND,
                                message="Dependency path not found in cache",
                            )
                        )
                        continue

                    chart_analysis = self.analyzer.analyze_chart_from_path(
                        dependency_path
                    )

                    resource_kinds = []
                    if chart_analysis.templates:
                        resource_kinds = sorted(
                            set([t.kind for t in chart_analysis.templates if t.kind])
                        )

                    dependencies_analysis.append(
                        DependencyAnalysisInfo(
                            name=dep_name,
                            success=True,
                            version=chart_analysis.metadata.version,
                            description=chart_analysis.metadata.description,
                            templates_count=len(chart_analysis.templates),
                            configuration_options_count=len(
                                chart_analysis.values_schema.properties
                            )
                            if chart_analysis.values_schema
                            and chart_analysis.values_schema.properties
                            else 0,
                            readme_available=bool(chart_analysis.readme),
                            kubernetes_resource_types=resource_kinds,
                        )
                    )

                except Exception as e:
                    dependencies_analysis.append(
                        DependencyAnalysisInfo(
                            name=dep_name,
                            success=False,
                            error=ErrorType.ANALYSIS_FAILED,
                            message=f"Failed to analyze cached dependency: {str(e)}",
                        )
                    )

            response = AnalyzeResponse(
                success=True,
                message="Dependencies analysis completed",
                cached_dependencies_count=len(cached_deps),
                dependencies=dependencies_analysis,
            )

            return [TextContent(type="text", text=response.model_dump_json(indent=2))]

        except Exception as e:
            error_response = AnalyzeResponse(
                success=False,
                error=ErrorType.ANALYSIS_FAILED,
                message="Error analyzing dependencies",
                details=str(e),
            )
            return [
                TextContent(type="text", text=error_response.model_dump_json(indent=2))
            ]

    async def _get_configuration_options(
        self, arguments: Dict[str, Any]
    ) -> List[TextContent]:
        """Get configuration options for a specific dependency based on its values.yaml file."""
        dependency_name = arguments["dependency_name"]

        dependency_path = self.dependency_cache.get_dependency_path(dependency_name)
        if not dependency_path:
            error_response = ConfigurationResponse(
                success=False,
                error=ErrorType.DEPENDENCY_NOT_FOUND,
                message=f"Dependency '{dependency_name}' not found in cache",
                action_required="run download_dependencies first",
            )
            return [
                TextContent(type="text", text=error_response.model_dump_json(indent=2))
            ]

        try:
            chart_analysis = self.analyzer.analyze_chart_from_path(dependency_path)
        except Exception as e:
            error_response = ConfigurationResponse(
                success=False,
                error=ErrorType.ANALYSIS_FAILED,
                message=f"Error analyzing cached dependency '{dependency_name}'",
                details=str(e),
            )
            return [
                TextContent(type="text", text=error_response.model_dump_json(indent=2))
            ]

        # Build schema information
        schema_data = None
        if chart_analysis.values_schema and chart_analysis.values_schema.properties:
            schema_data = {
                "type": chart_analysis.values_schema.type,
                "properties": chart_analysis.values_schema.properties,
                "required": chart_analysis.values_schema.required,
                "description": chart_analysis.values_schema.description,
            }

        # Get Kubernetes resource information
        kubernetes_resources = []
        if chart_analysis.templates:
            resource_kinds = [t.kind for t in chart_analysis.templates if t.kind]
            kubernetes_resources = sorted(set(resource_kinds))

        # Create a flattened configuration structure for easier AI consumption
        configuration_structure = {}
        if chart_analysis.default_values:
            configuration_structure = self._flatten_config_structure(
                chart_analysis.default_values
            )

        response = ConfigurationResponse(
            success=True,
            message="Configuration options retrieved successfully",
            dependency_name=dependency_name,
            version=chart_analysis.metadata.version,
            description=chart_analysis.metadata.description,
            values_schema=schema_data,
            default_values=chart_analysis.default_values,
            kubernetes_resources=kubernetes_resources,
            configuration_structure=configuration_structure,
        )

        return [TextContent(type="text", text=response.model_dump_json(indent=2))]

    def _flatten_config_structure(
        self, config: Dict[str, Any], prefix: str = ""
    ) -> Dict[str, str]:
        """Flatten nested configuration structure for AI consumption."""
        flattened = {}

        for key, value in config.items():
            full_key = f"{prefix}.{key}" if prefix else key

            if isinstance(value, dict):
                flattened.update(self._flatten_config_structure(value, full_key))
            elif isinstance(value, list):
                flattened[full_key] = (
                    f"array[{len(value)}] - {type(value[0]).__name__ if value else 'empty'}"
                )
            else:
                flattened[full_key] = (
                    f"{type(value).__name__} - {str(value)[:50]}{'...' if len(str(value)) > 50 else ''}"
                )

        return flattened

    async def _validate_dependency_values(
        self, arguments: Dict[str, Any]
    ) -> List[TextContent]:
        """Validate dependency values against inferred configuration structure."""
        dependency_name = arguments["dependency_name"]
        values = arguments["values"]

        try:
            # First check if the dependency is cached
            dependency_path = self.dependency_cache.get_dependency_path(dependency_name)
            if not dependency_path:
                error_response = ValidationResponse(
                    success=False,
                    error=ErrorType.DEPENDENCY_NOT_FOUND,
                    message=f"Dependency '{dependency_name}' not found in cache",
                    action_required="run download_dependencies first",
                )
                return [
                    TextContent(
                        type="text", text=error_response.model_dump_json(indent=2)
                    )
                ]

            # Analyze the cached dependency directly
            try:
                chart_analysis = self.analyzer.analyze_chart_from_path(dependency_path)
            except Exception as e:
                error_response = ValidationResponse(
                    success=False,
                    error=ErrorType.ANALYSIS_FAILED,
                    message=f"Error analyzing cached dependency '{dependency_name}'",
                    details=str(e),
                )
                return [
                    TextContent(
                        type="text", text=error_response.model_dump_json(indent=2)
                    )
                ]

            if not chart_analysis.default_values:
                error_response = ValidationResponse(
                    success=False,
                    error=ErrorType.NO_DEFAULT_VALUES,
                    message="No default values available - Cannot perform structure validation",
                    dependency_name=dependency_name,
                    version=chart_analysis.metadata.version,
                    has_default_values=False,
                )
                return [
                    TextContent(
                        type="text", text=error_response.model_dump_json(indent=2)
                    )
                ]

            # Check if provided values match expected structure
            def validate_structure(provided_vals, default_vals, path=""):
                issues = []

                if isinstance(default_vals, dict) and isinstance(provided_vals, dict):
                    # Check for unknown keys
                    for key in provided_vals:
                        if key not in default_vals:
                            issues.append(
                                ValidationIssue(
                                    type="unknown_key",
                                    path=f"{path}.{key}" if path else key,
                                    message=f"Unknown configuration key: {path}.{key}"
                                    if path
                                    else f"Unknown configuration key: {key}",
                                    severity=IssueSeverity.WARNING,
                                )
                            )
                        else:
                            # Recursively validate nested structures
                            nested_issues = validate_structure(
                                provided_vals[key],
                                default_vals[key],
                                f"{path}.{key}" if path else key,
                            )
                            issues.extend(nested_issues)

                elif isinstance(default_vals, list) and not isinstance(
                    provided_vals, list
                ):
                    issues.append(
                        ValidationIssue(
                            type="type_mismatch",
                            path=path,
                            expected_type="list",
                            actual_type=type(provided_vals).__name__,
                            message=f"Type mismatch at {path}: expected list, got {type(provided_vals).__name__}",
                            severity=IssueSeverity.ERROR,
                        )
                    )
                elif isinstance(default_vals, (str, int, float, bool)) and type(
                    provided_vals
                ) is not type(default_vals):
                    issues.append(
                        ValidationIssue(
                            type="type_mismatch",
                            path=path,
                            expected_type=type(default_vals).__name__,
                            actual_type=type(provided_vals).__name__,
                            message=f"Type mismatch at {path}: expected {type(default_vals).__name__}, got {type(provided_vals).__name__}",
                            severity=IssueSeverity.ERROR,
                        )
                    )

                return issues

            issues = validate_structure(values, chart_analysis.default_values)

            response = ValidationResponse(
                success=True,
                message="Validation completed successfully"
                if len(issues) == 0
                else "Validation completed with issues",
                dependency_name=dependency_name,
                version=chart_analysis.metadata.version,
                has_default_values=True,
                validation_passed=len(issues) == 0,
                issues=issues,
                summary=ValidationSummary(
                    total_issues=len(issues),
                    errors=len(
                        [i for i in issues if i.severity == IssueSeverity.ERROR]
                    ),
                    warnings=len(
                        [i for i in issues if i.severity == IssueSeverity.WARNING]
                    ),
                ),
            )

            return [TextContent(type="text", text=response.model_dump_json(indent=2))]

        except Exception as e:
            logger.error(f"Error validating dependency values: {e}")
            error_response = ValidationResponse(
                success=False,
                error=ErrorType.VALIDATION_FAILED,
                message="Error during validation",
                details=str(e),
            )
            return [
                TextContent(type="text", text=error_response.model_dump_json(indent=2))
            ]


async def main():
    """Main entry point for the MCP server."""
    server_instance = HelmChartDependencyMCPServer()

    async with stdio_server() as (read_stream, write_stream):
        await server_instance.server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="helm-chart-dependency-mcp",
                server_version="0.1.0",
                capabilities=server_instance.server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def cli_main():
    """Synchronous entry point for the CLI."""
    asyncio.run(main())


if __name__ == "__main__":
    cli_main()
