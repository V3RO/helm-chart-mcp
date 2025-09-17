"""Chart downloader for fetching Helm charts from OCI registries and HTTP repositories."""

from __future__ import annotations

import asyncio
import base64
import logging
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import aiofiles
import aiohttp

from .config import settings
from .exceptions import (
    AuthenticationError,
    ChartDownloadError,
    ChartNotFoundError,
)
from .models import ChartDependency

logger = logging.getLogger(__name__)


class ChartDownloader:
    """Downloads Helm charts from various sources."""

    def __init__(
        self,
        cache_dir: Optional[str] = None,
        timeout: Optional[int] = None,
        max_retries: Optional[int] = None,
    ):
        self.session: Optional[aiohttp.ClientSession] = None
        self.temp_dirs: List[Path] = []
        self.cache_dir = Path(cache_dir or settings.cache_config.directory)
        self._oci_auth_cache: Dict[str, str] = {}  # Cache for OCI auth tokens

        # Initialize timeout and max_retries from config
        http_config = settings.http_config
        self.timeout = timeout or http_config.timeout
        self.max_retries = max_retries or http_config.max_retries

    async def __aenter__(self):
        """Async context manager entry."""
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self.session:
            await self.session.close()
        self.cleanup_temp_dirs()

    def cleanup_temp_dirs(self) -> None:
        """Clean up temporary directories."""
        for temp_dir in self.temp_dirs:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
        self.temp_dirs.clear()

    def _get_oci_credentials(self, registry_host: str) -> Optional[Tuple[str, str]]:
        """Get OCI registry credentials from environment variables.

        Environment variables:
        - HELM_OCI_USERNAME: Username for OCI registry authentication
        - HELM_OCI_PASSWORD: Password for OCI registry authentication
        - HELM_OCI_REGISTRY: Optional - specific registry host to authenticate against

        Args:
            registry_host: The registry hostname to get credentials for

        Returns:
            Tuple of (username, password) if credentials are available, None otherwise
        """
        oci_config = settings.oci_config
        username = oci_config.username
        password = oci_config.password
        registry_filter = oci_config.registry

        if not username or not password:
            return None

        # If HELM_OCI_REGISTRY is set, only use credentials for that specific registry
        if registry_filter and registry_filter != registry_host:
            return None

        return (username, password)

    async def _get_oci_auth_token(
        self, registry_host: str, username: str, password: str
    ) -> Optional[str]:
        """Get OCI registry authentication token using Docker Registry HTTP API V2.

        This implements the Docker Registry authentication flow:
        1. Try to access a protected resource
        2. Parse the WWW-Authenticate header to get the auth service URL
        3. Request a token from the auth service using basic auth
        4. Return the bearer token for subsequent requests

        Args:
            registry_host: The registry hostname
            username: Username for authentication
            password: Password for authentication

        Returns:
            Bearer token if successful, None otherwise
        """
        if not self.session:
            raise RuntimeError("HTTP session not initialized")

        # Cache key for this registry and user
        cache_key = f"{registry_host}:{username}"
        if cache_key in self._oci_auth_cache:
            return self._oci_auth_cache[cache_key]

        try:
            # Step 1: Try to access the registry to get auth challenge
            test_url = f"https://{registry_host}/v2/"

            async with self.session.get(test_url) as response:
                if response.status == 200:
                    # No authentication required
                    return None
                elif response.status != 401:
                    logger.warning(
                        f"Unexpected response from registry {registry_host}: {response.status}"
                    )
                    return None

                # Parse WWW-Authenticate header
                www_auth = response.headers.get("WWW-Authenticate", "")
                if not www_auth.startswith("Bearer "):
                    logger.warning(
                        f"Unsupported authentication method for {registry_host}: {www_auth}"
                    )
                    return None

                # Parse bearer challenge to extract realm and service
                auth_params = {}
                for param in www_auth[7:].split(","):  # Remove "Bearer " prefix
                    if "=" in param:
                        key, value = param.strip().split("=", 1)
                        auth_params[key] = value.strip('"')

                realm = auth_params.get("realm")
                service = auth_params.get("service")

                if not realm:
                    logger.error(
                        f"No realm found in auth challenge from {registry_host}"
                    )
                    return None

            # Step 2: Request token from auth service
            auth_url = realm
            if service:
                auth_url += f"?service={service}"

            # Use basic authentication
            auth_header = base64.b64encode(f"{username}:{password}".encode()).decode()

            async with self.session.get(
                auth_url, headers={"Authorization": f"Basic {auth_header}"}
            ) as response:
                if response.status != 200:
                    logger.error(
                        f"Failed to get auth token from {auth_url}: HTTP {response.status}"
                    )
                    return None

                token_response = await response.json()
                token = token_response.get("token") or token_response.get(
                    "access_token"
                )

                if token:
                    # Cache the token
                    self._oci_auth_cache[cache_key] = token
                    logger.info(f"Successfully obtained auth token for {registry_host}")
                    return token
                else:
                    logger.error(f"No token found in auth response from {auth_url}")
                    return None

        except Exception as e:
            logger.error(f"Failed to get OCI auth token for {registry_host}: {e}")
            return None

    async def download_dependency(self, dependency: ChartDependency) -> Path:
        """Download a chart dependency and return path to extracted chart directory.

        Args:
            dependency: Chart dependency to download

        Returns:
            Path to the extracted chart directory

        Raises:
            ValueError: If repository is not specified or unsupported
            RuntimeError: If download fails
        """
        if not dependency.repository:
            raise ChartDownloadError(
                f"No repository specified for dependency {dependency.name}"
            )

        repository = dependency.repository.strip()
        assert repository is not None  # For type checker

        if repository.startswith("oci://"):
            return await self._download_oci_chart(dependency)
        elif repository.startswith(("http://", "https://")):
            return await self._download_http_chart(dependency)
        elif repository.startswith("file://"):
            # Local file repository - for development/testing
            return await self._handle_local_chart(dependency)
        else:
            raise ChartDownloadError(f"Unsupported repository type: {repository}")

    async def _download_oci_chart(self, dependency: ChartDependency) -> Path:
        """Download chart from OCI registry with authentication support.

        Args:
            dependency: Chart dependency to download from OCI registry

        Returns:
            Path to the extracted chart directory

        Raises:
            RuntimeError: If download or authentication fails
        """
        if not self.session:
            raise RuntimeError("HTTP session not initialized")

        assert dependency.repository is not None
        # Parse OCI URL: oci://registry.example.com/path/to/chart
        oci_url = dependency.repository[6:]  # Remove 'oci://' prefix
        registry_host = oci_url.split("/")[0]
        chart_path = "/".join(oci_url.split("/")[1:])

        chart_name = dependency.name
        chart_version = dependency.version

        logger.info(
            f"Downloading OCI chart {chart_name}:{chart_version} from {registry_host}"
        )

        headers = {}

        # Check for OCI credentials
        credentials = self._get_oci_credentials(registry_host)
        if credentials:
            username, password = credentials
            logger.info(
                f"Found OCI credentials for registry {registry_host}, attempting authentication"
            )

            # For certain registries, prefer basic auth over token auth
            if registry_host == "registry.lab.dynatrace.org":
                logger.info(
                    f"Using basic authentication for {registry_host} (preferred for this registry)"
                )
                basic_auth = base64.b64encode(
                    f"{username}:{password}".encode()
                ).decode()
                headers["Authorization"] = f"Basic {basic_auth}"
            else:
                try:
                    # Get authentication token
                    token = await self._get_oci_auth_token(
                        registry_host, username, password
                    )
                    if token:
                        headers["Authorization"] = f"Bearer {token}"
                        logger.info(
                            f"Using bearer token authentication for {registry_host}"
                        )
                    else:
                        # Fallback to basic auth if token auth fails
                        basic_auth = base64.b64encode(
                            f"{username}:{password}".encode()
                        ).decode()
                        headers["Authorization"] = f"Basic {basic_auth}"
                        logger.info(f"Using basic authentication for {registry_host}")
                except Exception as e:
                    logger.warning(f"Failed to get auth token, trying basic auth: {e}")
                    basic_auth = base64.b64encode(
                        f"{username}:{password}".encode()
                    ).decode()
                    headers["Authorization"] = f"Basic {basic_auth}"

        # OCI distribution spec endpoints
        manifest_url = f"https://{registry_host}/v2/{chart_path}/{chart_name}/manifests/{chart_version}"

        try:
            # Get manifest with authentication
            async with self.session.get(manifest_url, headers=headers) as response:
                if response.status == 401:
                    error_msg = (
                        f"Authentication failed for OCI registry {registry_host}"
                    )
                    if credentials:
                        error_msg += ". Verify your HELM_OCI_USERNAME and HELM_OCI_PASSWORD environment variables."
                    else:
                        error_msg += ". Set HELM_OCI_USERNAME and HELM_OCI_PASSWORD environment variables for authentication."
                    raise AuthenticationError(error_msg)
                elif response.status == 403:
                    raise AuthenticationError(
                        f"Access denied to {chart_name} in registry {registry_host}. Check repository permissions."
                    )
                elif response.status == 404:
                    raise ChartNotFoundError(
                        f"Chart {chart_name}:{chart_version} not found in registry {registry_host}"
                    )
                elif response.status != 200:
                    raise ChartDownloadError(
                        f"Failed to fetch manifest: HTTP {response.status}"
                    )

                manifest = await response.json()
                logger.info(
                    f"Successfully retrieved manifest for {chart_name}:{chart_version}"
                )

            # Find the chart layer (should be the first layer with mediaType application/vnd.cncf.helm.chart.content.v1.tar+gzip)
            chart_layer = None
            for layer in manifest.get("layers", []):
                if (
                    layer.get("mediaType")
                    == "application/vnd.cncf.helm.chart.content.v1.tar+gzip"
                ):
                    chart_layer = layer
                    break

            if not chart_layer:
                raise ChartDownloadError(
                    f"No chart layer found in manifest for {chart_name}"
                )

            # Download the chart blob
            blob_digest = chart_layer["digest"]
            blob_url = f"https://{registry_host}/v2/{chart_path}/{chart_name}/blobs/{blob_digest}"

            # Create temporary file for the chart
            temp_dir = Path(tempfile.mkdtemp(prefix="helm_chart_oci_"))
            self.temp_dirs.append(temp_dir)

            chart_file = temp_dir / f"{chart_name}-{chart_version}.tgz"

            logger.info(f"Downloading chart blob from {blob_url}")

            # Use the same auth headers for blob download
            async with self.session.get(blob_url, headers=headers) as response:
                if response.status == 401:
                    raise AuthenticationError(
                        f"Authentication failed when downloading chart blob from {registry_host}"
                    )
                elif response.status != 200:
                    raise ChartDownloadError(
                        f"Failed to download chart blob: HTTP {response.status}"
                    )

                async with aiofiles.open(chart_file, "wb") as f:
                    async for chunk in response.content.iter_chunked(8192):
                        await f.write(chunk)

            logger.info(f"Successfully downloaded chart to {chart_file}")

            # Extract the chart
            return await self._extract_chart(chart_file)

        except Exception as e:
            logger.error(
                f"Failed to download OCI chart {chart_name}:{chart_version} from {dependency.repository}: {e}"
            )
            raise ChartDownloadError(f"Failed to download OCI chart: {e}")

    async def _download_http_chart(self, dependency: ChartDependency) -> Path:
        """Download chart from HTTP repository.

        Args:
            dependency: Chart dependency to download from HTTP repository

        Returns:
            Path to the extracted chart directory

        Raises:
            RuntimeError: If download fails
        """
        if not self.session:
            raise RuntimeError("HTTP session not initialized")

        assert dependency.repository is not None

        # First try to get the repository index to find the actual chart URL
        try:
            chart_url = await self._resolve_chart_url_from_index(dependency)
        except Exception as e:
            logger.warning(
                f"Could not resolve chart URL from index: {e}, trying direct download"
            )
            # Fallback to direct URL construction
            chart_url = f"{dependency.repository.rstrip('/')}/{dependency.name}-{dependency.version}.tgz"

        try:
            logger.info(f"Downloading chart from: {chart_url}")
            async with self.session.get(
                chart_url, timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as response:
                logger.info(f"HTTP response status: {response.status}")
                if response.status != 200:
                    response_text = await response.text()
                    logger.error(
                        f"HTTP error response: {response_text[:500]}..."
                    )  # Truncate long HTML responses
                    raise ChartDownloadError(
                        f"Failed to download chart: HTTP {response.status}"
                    )

                # Create temporary file for the chart
                temp_dir = Path(tempfile.mkdtemp(prefix="helm_chart_http_"))
                self.temp_dirs.append(temp_dir)

                chart_file = temp_dir / f"{dependency.name}-{dependency.version}.tgz"

                logger.info(f"Saving chart to: {chart_file}")
                async with aiofiles.open(chart_file, "wb") as f:
                    async for chunk in response.content.iter_chunked(8192):
                        await f.write(chunk)

                # Verify file was downloaded
                if not chart_file.exists() or chart_file.stat().st_size == 0:
                    raise ChartDownloadError(
                        f"Downloaded chart file is empty or doesn't exist: {chart_file}"
                    )

                logger.info(
                    f"Chart downloaded successfully, size: {chart_file.stat().st_size} bytes"
                )

                # Extract the chart
                return await self._extract_chart(chart_file)

        except asyncio.TimeoutError:
            logger.error(
                f"Timeout downloading chart {dependency.name}:{dependency.version} from {chart_url}"
            )
            raise ChartDownloadError(f"Timeout downloading chart from {chart_url}")
        except Exception as e:
            logger.error(
                f"Failed to download HTTP chart {dependency.name}:{dependency.version} from {chart_url}: {e}"
            )
            raise ChartDownloadError(f"Failed to download HTTP chart: {e}")

    async def _resolve_chart_url_from_index(self, dependency: ChartDependency) -> str:
        """Resolve the actual chart download URL from the Helm repository index.

        Args:
            dependency: Chart dependency to resolve URL for

        Returns:
            Resolved chart download URL

        Raises:
            RuntimeError: If index cannot be fetched or chart not found
        """
        if not self.session:
            raise RuntimeError("HTTP session not initialized")

        assert dependency.repository is not None

        # Get the repository index.yaml
        index_url = f"{dependency.repository.rstrip('/')}/index.yaml"
        logger.info(f"Fetching repository index from: {index_url}")

        async with self.session.get(
            index_url, timeout=aiohttp.ClientTimeout(total=self.timeout // 2)
        ) as response:
            if response.status != 200:
                raise ChartDownloadError(
                    f"Failed to fetch repository index: HTTP {response.status}"
                )

            index_content = await response.text()
            import yaml

            index_data = yaml.safe_load(index_content)

            # Find the chart in the index
            entries = index_data.get("entries", {})
            chart_entries = entries.get(dependency.name, [])

            # Find the specific version
            for entry in chart_entries:
                if entry.get("version") == dependency.version:
                    urls = entry.get("urls", [])
                    if urls:
                        chart_url = urls[0]
                        # If URL is relative, make it absolute
                        if not chart_url.startswith(("http://", "https://")):
                            chart_url = (
                                f"{dependency.repository.rstrip('/')}/{chart_url}"
                            )
                        logger.info(f"Resolved chart URL from index: {chart_url}")
                        return chart_url

            raise ChartNotFoundError(
                f"Chart {dependency.name} version {dependency.version} not found in repository index"
            )

    async def _handle_local_chart(self, dependency: ChartDependency) -> Path:
        """Handle local file repository.

        Args:
            dependency: Chart dependency with file:// repository

        Returns:
            Path to the extracted chart directory

        Raises:
            FileNotFoundError: If local chart file doesn't exist
        """
        assert dependency.repository is not None
        # file://path/to/charts -> path/to/charts/chart-name-version.tgz
        local_path = dependency.repository[7:]  # Remove 'file://' prefix
        chart_file = Path(local_path) / f"{dependency.name}-{dependency.version}.tgz"

        if not chart_file.exists():
            raise ChartNotFoundError(f"Local chart file not found: {chart_file}")

        return await self._extract_chart(chart_file)

    async def _extract_chart(self, chart_file: Path) -> Path:
        """Extract a chart archive and return the path to the chart directory.

        Args:
            chart_file: Path to the chart archive file

        Returns:
            Path to the extracted chart directory

        Raises:
            ValueError: If no chart directory found in archive
        """
        extract_dir = chart_file.parent / "extracted"
        extract_dir.mkdir(exist_ok=True)

        # Extract the tar.gz file
        with tarfile.open(chart_file, "r:gz") as tar:
            tar.extractall(extract_dir)

        # Find the chart directory (usually the first directory in the archive)
        chart_dirs = [d for d in extract_dir.iterdir() if d.is_dir()]
        if not chart_dirs:
            raise ChartDownloadError(f"No chart directory found in {chart_file}")

        return chart_dirs[0]

    async def download_multiple_dependencies(
        self, dependencies: List[ChartDependency]
    ) -> Dict[str, Path]:
        """Download multiple dependencies concurrently.

        Args:
            dependencies: List of chart dependencies to download

        Returns:
            Dict mapping dependency names to their extracted chart paths
        """
        tasks = []
        for dependency in dependencies:
            task = asyncio.create_task(self._download_with_retry(dependency))
            tasks.append((dependency.name, task))

        results = {}
        for dep_name, task in tasks:
            try:
                chart_path = await task
                results[dep_name] = chart_path
            except Exception as e:
                logger.error(f"Failed to download dependency {dep_name}: {e}")
                # Continue with other downloads

        return results

    async def _download_with_retry(
        self, dependency: ChartDependency, max_retries: int = None
    ) -> Path:
        """Download with retry logic.

        Args:
            dependency: Chart dependency to download
            max_retries: Maximum number of retry attempts

        Returns:
            Path to the extracted chart directory

        Raises:
            RuntimeError: If all retry attempts fail
        """
        last_error = None
        http_config = settings.http_config
        self.timeout = http_config.timeout
        self.max_retries = http_config.max_retries
        max_retries = max_retries or http_config.max_retries

        for attempt in range(max_retries):
            try:
                return await self.download_dependency(dependency)
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait_time = 2**attempt  # Exponential backoff
                    logger.warning(
                        f"Download attempt {attempt + 1} failed for {dependency.name}, retrying in {wait_time}s: {e}"
                    )
                    await asyncio.sleep(http_config.retry_delay)
                else:
                    logger.error(f"All download attempts failed for {dependency.name}")

        raise last_error or ChartDownloadError(f"Failed to download {dependency.name}")
