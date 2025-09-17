"""Dependency cache manager for Helm charts."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Set, Any

import yaml

from .chart_downloader import ChartDownloader
from .config import settings
from .models import ChartDependency

logger = logging.getLogger(__name__)


class DependencyCacheManager:
    """Manages a local cache of downloaded Helm chart dependencies."""

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        """Initialize the dependency cache manager.

        Args:
            cache_dir: Directory to store cached dependencies. If None, uses a temp directory.
        """
        if cache_dir is None:
            self.cache_dir = Path(tempfile.mkdtemp(prefix="helm_chart_cache_"))
        else:
            self.cache_dir = Path(cache_dir or settings.cache_config.directory)

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_file = self.cache_dir / settings.cache_config.metadata_filename

        self._cache_metadata = self._load_cache_metadata()

    def _load_cache_metadata(self) -> Dict[str, Any]:
        """Load cache metadata from disk."""
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load cache metadata: {e}")

        return {
            "charts": {},
            "dependencies": {},
        }

    def _save_cache_metadata(self) -> None:
        """Save cache metadata to disk."""
        try:
            with open(self.metadata_file, "w") as f:
                json.dump(self._cache_metadata, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save cache metadata: {e}")

    def _get_chart_yaml_hash(self, chart_yaml_content: str) -> str:
        """Get a hash for the Chart.yaml content to detect changes."""
        return hashlib.sha256(chart_yaml_content.encode("utf-8")).hexdigest()[:16]

    def _parse_dependencies(self, chart_yaml_content: str) -> List[Dict[str, Any]]:
        """Parse dependencies from Chart.yaml content."""
        try:
            chart_data = yaml.safe_load(chart_yaml_content)
            return chart_data.get("dependencies", [])
        except yaml.YAMLError as e:
            logger.error(f"Failed to parse Chart.yaml: {e}")
            return []

    def get_cached_dependencies(
        self, chart_yaml_content: str
    ) -> Optional[Dict[str, Path]]:
        """Get cached dependencies for a Chart.yaml if they exist and are current.

        Args:
            chart_yaml_content: Content of the Chart.yaml file

        Returns:
            Dict mapping dependency names to their cached paths, or None if cache miss
        """
        chart_hash = self._get_chart_yaml_hash(chart_yaml_content)

        if chart_hash not in self._cache_metadata["charts"]:
            return None

        chart_cache = self._cache_metadata["charts"][chart_hash]
        cached_deps = chart_cache.get("dependencies", {})

        # Verify all cached dependencies still exist
        for dep_name, dep_path in cached_deps.items():
            if not Path(dep_path).exists():
                logger.info(
                    f"Cached dependency {dep_name} no longer exists, invalidating cache"
                )
                return None

        return {name: Path(path) for name, path in cached_deps.items()}

    async def download_and_cache_dependencies(
        self, chart_yaml_content: str
    ) -> Dict[str, Path]:
        """Download and cache all dependencies from a Chart.yaml.

        Args:
            chart_yaml_content: Content of the Chart.yaml file

        Returns:
            Dict mapping dependency names to their cached paths
        """
        chart_hash = self._get_chart_yaml_hash(chart_yaml_content)
        dependencies = self._parse_dependencies(chart_yaml_content)

        if not dependencies:
            logger.info("No dependencies found in Chart.yaml")
            return {}

        # Check if we already have current cached dependencies
        cached_deps = self.get_cached_dependencies(chart_yaml_content)
        if cached_deps is not None:
            logger.info(f"Using cached dependencies for chart hash {chart_hash}")
            return cached_deps

        logger.info(
            f"Downloading {len(dependencies)} dependencies for chart hash {chart_hash}"
        )

        downloaded_deps = {}

        async with ChartDownloader() as downloader:
            for dep_config in dependencies:
                dep_name = dep_config.get("name")
                if not dep_name:
                    logger.warning("Dependency missing name, skipping")
                    continue

                try:
                    # Create dependency cache directory
                    deps_dir = self.cache_dir / "dependencies" / dep_name
                    deps_dir.mkdir(parents=True, exist_ok=True)

                    # Download the dependency
                    chart_dependency = ChartDependency(
                        name=dep_name,
                        version=dep_config.get("version", ""),
                        repository=dep_config.get("repository", ""),
                        alias=dep_config.get("alias"),
                        **{"import-values": dep_config.get("import-values")},
                    )

                    temp_dep_path = await downloader.download_dependency(
                        chart_dependency
                    )

                    # Copy the downloaded dependency to permanent cache
                    if temp_dep_path and temp_dep_path.exists():
                        # Copy the entire dependency directory to cache
                        shutil.copytree(
                            temp_dep_path, deps_dir / "charts", dirs_exist_ok=True
                        )
                        permanent_dep_path = deps_dir / "charts"
                        downloaded_deps[dep_name] = permanent_dep_path
                        logger.info(
                            f"Downloaded dependency {dep_name} to {permanent_dep_path}"
                        )
                    else:
                        logger.warning(
                            f"Downloaded dependency {dep_name} path does not exist: {temp_dep_path}"
                        )

                except Exception as e:
                    logger.error(f"Failed to download dependency {dep_name}: {e}")
                    # Don't fail the entire operation for one dependency
                    continue

        # Update cache metadata
        self._cache_metadata["charts"][chart_hash] = {
            "dependencies": {name: str(path) for name, path in downloaded_deps.items()},
            "timestamp": str(Path().stat().st_mtime),
            "chart_yaml_content": chart_yaml_content,
        }

        # Update dependency references
        for dep_name, dep_path in downloaded_deps.items():
            if dep_name not in self._cache_metadata["dependencies"]:
                self._cache_metadata["dependencies"][dep_name] = {
                    "path": str(dep_path),
                    "charts": [],
                }

            if (
                chart_hash
                not in self._cache_metadata["dependencies"][dep_name]["charts"]
            ):
                self._cache_metadata["dependencies"][dep_name]["charts"].append(
                    chart_hash
                )

        self._save_cache_metadata()

        return downloaded_deps

    def get_dependency_path(self, dependency_name: str) -> Optional[Path]:
        """Get the cached path for a specific dependency.

        Args:
            dependency_name: Name of the dependency to find

        Returns:
            Path to the cached dependency, or None if not found
        """
        if dependency_name not in self._cache_metadata["dependencies"]:
            return None

        dep_info = self._cache_metadata["dependencies"][dependency_name]
        dep_path = Path(dep_info["path"])

        if not dep_path.exists():
            logger.warning(f"Cached dependency {dependency_name} path no longer exists")
            return None

        return dep_path

    def list_cached_dependencies(self) -> Set[str]:
        """Get a set of all cached dependency names.

        Returns:
            Set of cached dependency names
        """
        return set(self._cache_metadata["dependencies"].keys())

    def clear_cache(self) -> None:
        """Clear all cached dependencies."""
        if self.cache_dir.exists():
            shutil.rmtree(self.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_metadata = {"charts": {}, "dependencies": {}}
        self._save_cache_metadata()

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get statistics about the cache.

        Returns:
            Dictionary with cache statistics
        """
        return {
            "cache_dir": str(self.cache_dir),
            "cached_charts": len(self._cache_metadata["charts"]),
            "cached_dependencies": len(self._cache_metadata["dependencies"]),
            "total_size_bytes": sum(
                sum(f.stat().st_size for f in Path(root).rglob("*") if f.is_file())
                for root, _, _ in os.walk(self.cache_dir)
            )
            if self.cache_dir.exists()
            else 0,
        }
