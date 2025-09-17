"""Helm chart dependency analyzer for extracting schema information from chart packages."""

from __future__ import annotations

import logging
import re
import shutil
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Union

import yaml

from .chart_downloader import ChartDownloader
from .config import SchemaTypes
from .exceptions import (
    ChartAnalysisError,
    InvalidChartError,
)
from .models import (
    ChartAnalysis,
    ChartMetadata,
    ChartTemplate,
    DependencyAnalysis,
    ValuesSchema,
)

logger = logging.getLogger(__name__)


class ChartDependencyAnalyzer:
    """Analyzes Helm chart dependencies and extracts schema information."""

    def __init__(self):
        self.temp_dirs: List[Path] = []

    def __del__(self) -> None:
        """Clean up temporary directories."""
        self.cleanup()

    def cleanup(self) -> None:
        """Clean up temporary directories."""
        for temp_dir in self.temp_dirs:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
        self.temp_dirs.clear()

    def analyze_chart_from_path(self, chart_path: Union[str, Path]) -> ChartAnalysis:
        """Analyze a Helm chart from a directory path.

        Args:
            chart_path: Path to chart directory or .tgz file

        Returns:
            Complete chart analysis

        Raises:
            FileNotFoundError: If chart path doesn't exist
            ValueError: If chart path is invalid
        """
        chart_path = Path(chart_path)

        if not chart_path.exists():
            raise FileNotFoundError(f"Chart path does not exist: {chart_path}")

        if chart_path.is_file() and chart_path.suffix == ".tgz":
            return self.analyze_chart_from_tgz(chart_path)
        elif chart_path.is_dir():
            return self.analyze_chart_from_directory(chart_path)
        else:
            raise ValueError(f"Invalid chart path: {chart_path}")

    def analyze_chart_from_tgz(self, tgz_path: Union[str, Path]) -> ChartAnalysis:
        """Extract and analyze a Helm chart from a .tgz file.

        Args:
            tgz_path: Path to chart .tgz archive

        Returns:
            Complete chart analysis

        Raises:
            FileNotFoundError: If archive doesn't exist
            ValueError: If no chart directory found in archive
            RuntimeError: If analysis fails
        """
        tgz_path = Path(tgz_path)

        if not tgz_path.exists():
            raise FileNotFoundError(f"Chart archive does not exist: {tgz_path}")

        # Create temporary directory
        temp_dir = Path(tempfile.mkdtemp(prefix="helm_chart_analysis_"))
        self.temp_dirs.append(temp_dir)

        try:
            # Extract the tar.gz file
            with tarfile.open(tgz_path, "r:gz") as tar:
                tar.extractall(temp_dir)

            # Find the chart directory (usually the first directory in the archive)
            chart_dirs = [d for d in temp_dir.iterdir() if d.is_dir()]
            if not chart_dirs:
                raise ValueError(f"No chart directory found in {tgz_path}")

            chart_dir = chart_dirs[0]
            analysis = self.analyze_chart_from_directory(chart_dir)
            analysis.chart_path = str(tgz_path)
            analysis.is_dependency = True

            return analysis

        except Exception as e:
            raise ChartAnalysisError(f"Failed to analyze chart from {tgz_path}: {e}")

    def analyze_chart_from_directory(self, chart_dir: Path) -> ChartAnalysis:
        """Analyze a Helm chart from a directory.

        Args:
            chart_dir: Path to chart directory

        Returns:
            Complete chart analysis

        Raises:
            FileNotFoundError: If Chart.yaml not found
        """
        chart_dir = Path(chart_dir)

        # Read Chart.yaml
        chart_yaml_path = chart_dir / "Chart.yaml"
        values_yaml_path = chart_dir / "values.yaml"
        if not chart_yaml_path.exists():
            raise InvalidChartError(f"Chart.yaml not found in {chart_dir}")

        with open(chart_yaml_path, "r", encoding="utf-8") as f:
            chart_data = yaml.safe_load(f)

        metadata = ChartMetadata(**chart_data)

        # Read values.yaml if it exists
        default_values = None
        if values_yaml_path.exists():
            with open(values_yaml_path, "r", encoding="utf-8") as f:
                default_values = yaml.safe_load(f) or {}

        # Generate configuration structure from default values
        values_schema = None
        if default_values:
            values_schema = self._generate_values_schema(default_values)

        # Read templates
        templates = self._read_templates(chart_dir / "templates")

        # Read README if it exists
        readme = None
        readme_paths = [
            chart_dir / "README.md",
            chart_dir / "README.txt",
            chart_dir / "README",
        ]
        for readme_path in readme_paths:
            if readme_path.exists():
                with open(readme_path, "r", encoding="utf-8") as f:
                    readme = f.read()
                break

        # Read NOTES.txt if it exists
        notes = None
        templates_dir = chart_dir / "templates" / "NOTES.txt"
        if templates_dir.exists():
            with open(templates_dir, "r", encoding="utf-8") as f:
                notes = f.read()

        return ChartAnalysis(
            metadata=metadata,
            values_schema=values_schema,
            default_values=default_values,
            templates=templates,
            readme=readme,
            notes=notes,
            chart_path=str(chart_dir),
        )

    def analyze_dependencies_from_chart(
        self, chart_path: Union[str, Path]
    ) -> DependencyAnalysis:
        """Analyze all dependencies of a parent chart.

        Args:
            chart_path: Path to parent chart directory

        Returns:
            Analysis of all chart dependencies
        """
        chart_path = Path(chart_path)

        # Analyze the parent chart
        parent_analysis = self.analyze_chart_from_path(chart_path)

        dependencies = []
        dependency_values = {}

        if parent_analysis.metadata.dependencies:
            # Look for dependency charts in charts/ directory
            charts_dir = (
                chart_path / "charts"
                if chart_path.is_dir()
                else chart_path.parent / "charts"
            )

            if charts_dir.exists():
                for dependency in parent_analysis.metadata.dependencies:
                    dep_name = dependency.alias or dependency.name

                    # Look for the dependency chart file
                    dep_file_pattern = f"{dependency.name}-{dependency.version}.tgz"
                    dep_files = list(charts_dir.glob(dep_file_pattern))

                    if dep_files:
                        try:
                            dep_analysis = self.analyze_chart_from_tgz(dep_files[0])
                            dependencies.append(dep_analysis)

                            # Extract values configured for this dependency from parent values
                            if (
                                parent_analysis.default_values
                                and dep_name in parent_analysis.default_values
                            ):
                                dependency_values[dep_name] = (
                                    parent_analysis.default_values[dep_name]
                                )

                        except Exception as e:
                            print(
                                f"Warning: Failed to analyze dependency {dependency.name}: {e}"
                            )

        return DependencyAnalysis(
            parent_chart=parent_analysis.metadata.name,
            dependencies=dependencies,
            dependency_values=dependency_values,
            analysis_timestamp=datetime.now().isoformat(),
        )

    async def analyze_dependencies_from_yaml(
        self, chart_yaml_content: str, values_yaml_content: Optional[str] = None
    ) -> DependencyAnalysis:
        """Analyze chart dependencies from Chart.yaml content by downloading dependencies.

        Args:
            chart_yaml_content: Content of Chart.yaml file
            values_yaml_content: Optional content of values.yaml file

        Returns:
            Analysis of all chart dependencies

        Raises:
            ValueError: If Chart.yaml content cannot be parsed
            RuntimeError: If dependency analysis fails
        """
        try:
            # Parse Chart.yaml content
            chart_data = yaml.safe_load(chart_yaml_content)
            metadata = ChartMetadata(**chart_data)

            # Parse values.yaml content if provided
            default_values = None
            if values_yaml_content:
                try:
                    default_values = yaml.safe_load(values_yaml_content)
                except yaml.YAMLError as e:
                    print(f"Warning: Failed to parse values.yaml: {e}")

            dependencies = []
            dependency_values = {}

            if metadata.dependencies:
                print(
                    f"Found {len(metadata.dependencies)} dependencies to download and analyze"
                )
                # Download and analyze dependencies
                async with ChartDownloader() as downloader:
                    # Download all dependencies concurrently
                    print("Starting concurrent download of dependencies...")
                    downloaded_charts = await downloader.download_multiple_dependencies(
                        metadata.dependencies
                    )
                    print(
                        f"Downloaded {len(downloaded_charts)} out of {len(metadata.dependencies)} dependencies"
                    )

                    for dependency in metadata.dependencies:
                        dep_name = dependency.alias or dependency.name
                        print(
                            f"Processing dependency: {dep_name} from {dependency.repository}"
                        )

                        if dep_name in downloaded_charts:
                            try:
                                chart_path = downloaded_charts[dep_name]
                                print(f"Analyzing chart at: {chart_path}")
                                dep_analysis = self.analyze_chart_from_directory(
                                    chart_path
                                )
                                dependencies.append(dep_analysis)
                                print(f"Successfully analyzed {dep_name}")

                                # Extract values configured for this dependency from parent values
                                if default_values and dep_name in default_values:
                                    dependency_values[dep_name] = default_values[
                                        dep_name
                                    ]

                            except Exception as e:
                                print(
                                    f"Warning: Failed to analyze dependency {dependency.name}: {e}"
                                )
                                import traceback

                                traceback.print_exc()
                        else:
                            print(
                                f"Warning: Failed to download dependency {dependency.name} from {dependency.repository}"
                            )
            else:
                print("No dependencies found in Chart.yaml")

            return DependencyAnalysis(
                parent_chart=metadata.name,
                dependencies=dependencies,
                dependency_values=dependency_values,
                analysis_timestamp=datetime.now().isoformat(),
            )

        except yaml.YAMLError as e:
            raise InvalidChartError(f"Failed to parse Chart.yaml content: {e}")
        except Exception as e:
            raise ChartAnalysisError(f"Failed to analyze dependencies: {e}")

    def _generate_values_schema(self, values: Dict[str, Any]) -> ValuesSchema:
        """Generate configuration structure by inferring types from default values.

        Args:
            values: Default values dictionary from values.yaml

        Returns:
            Inferred schema for the values
        """

        def infer_type_and_schema(value: Any) -> Dict[str, Any]:
            """Infer JSON schema from a value."""
            if value is None:
                return {"type": SchemaTypes.NULL}
            elif isinstance(value, bool):
                return {"type": SchemaTypes.BOOLEAN}
            elif isinstance(value, int):
                return {"type": SchemaTypes.INTEGER}
            elif isinstance(value, float):
                return {"type": SchemaTypes.NUMBER}
            elif isinstance(value, str):
                return {"type": SchemaTypes.STRING}
            elif isinstance(value, list):
                if not value:
                    return {"type": SchemaTypes.ARRAY}
                # Infer items schema from first element
                item_schema = infer_type_and_schema(value[0])
                return {"type": SchemaTypes.ARRAY, "items": item_schema}
            elif isinstance(value, dict):
                properties = {}
                required = []
                for k, v in value.items():
                    properties[k] = infer_type_and_schema(v)
                    if v is not None:
                        required.append(k)
                return {
                    "type": SchemaTypes.OBJECT,
                    "properties": properties,
                    "required": required if required else [],
                }
            else:
                return {"type": SchemaTypes.STRING}  # fallback

        schema_data = infer_type_and_schema(values)

        return ValuesSchema(
            properties=schema_data.get("properties", {}),
            required=schema_data.get("required", []),
            type=SchemaTypes.OBJECT,
            description="Generated schema from default values.yaml",
        )

    def _read_templates(self, templates_dir: Path) -> List[ChartTemplate]:
        """Read all template files from the templates directory.

        Args:
            templates_dir: Path to templates directory

        Returns:
            List of parsed chart templates
        """
        templates = []

        if not templates_dir.exists():
            return templates

        for template_file in templates_dir.rglob("*"):
            if (
                template_file.is_file()
                and template_file.suffix in [".yaml", ".yml"]
                and template_file.name != "NOTES.txt"
            ):
                try:
                    with open(template_file, "r", encoding="utf-8") as f:
                        content = f.read()

                    # Try to detect Kubernetes resource kind
                    kind = self._extract_kind_from_template(content)

                    relative_path = template_file.relative_to(templates_dir)

                    templates.append(
                        ChartTemplate(
                            name=template_file.stem,
                            path=str(relative_path),
                            content=content,
                            kind=kind,
                        )
                    )

                except Exception as e:
                    logger.warning(f"Failed to read template {template_file}: {e}")

        return templates

    def _extract_kind_from_template(self, content: str) -> Optional[str]:
        """Extract Kubernetes resource kind from template content.

        Args:
            content: Template file content

        Returns:
            Kubernetes resource kind if found, None otherwise
        """
        # Look for 'kind:' in the template
        kind_match = re.search(r"^kind:\s*(.+)$", content, re.MULTILINE)
        if kind_match:
            kind = kind_match.group(1).strip()
            # Remove Helm template syntax if present
            kind = re.sub(r"\{\{.*?\}\}", "", kind).strip()
            return kind if kind else None
        return None

    def get_dependency_configuration_summary(
        self, dependency_analysis: DependencyAnalysis
    ) -> Dict[str, Any]:
        """Generate a summary of dependency configuration options for AI agents.

        Args:
            dependency_analysis: Complete dependency analysis

        Returns:
            Summary dictionary with configuration information
        """
        summary = {
            "parent_chart": dependency_analysis.parent_chart,
            "dependencies": {},
            "configured_values": dependency_analysis.dependency_values,
            "analysis_timestamp": dependency_analysis.analysis_timestamp,
        }

        for dep in dependency_analysis.dependencies:
            dep_name = dep.metadata.name
            dep_info = {
                "name": dep.metadata.name,
                "version": dep.metadata.version,
                "description": dep.metadata.description,
                "schema": None,
                "default_values": dep.default_values,
                "templates": [
                    {"name": t.name, "kind": t.kind, "path": t.path}
                    for t in dep.templates
                ],
                "readme_available": dep.readme is not None,
            }

            if dep.values_schema:
                dep_info["schema"] = {
                    "properties": dep.values_schema.properties,
                    "required": dep.values_schema.required,
                }

            summary["dependencies"][dep_name] = dep_info

        return summary
