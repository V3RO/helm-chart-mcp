# Helm Chart MCP Server

A Model Context Protocol (MCP) server that provides intelligent Helm chart dependency analysis and configuration assistance. This tool helps developers understand, configure, and validate Helm chart dependencies by analyzing Chart.yaml files, downloading dependencies, and providing configuration guidance.

## Features

- **Dependency Analysis**: Analyze Chart.yaml dependencies and understand their structure
- **Configuration Options**: Get detailed configuration options for chart dependencies
- **Values Validation**: Validate values.yaml configurations against dependency schemas
- **Chart Download**: Automatically download and cache chart dependencies

## Use Cases

- Configure complex Helm charts with multiple dependencies
- Understand available configuration options for chart dependencies
- Validate Helm values before deployment
- Get assistance with sidecar container configurations
- Troubleshoot chart dependency issues

## Setup

### Prerequisites

- Docker installed and running
- VS Code with MCP support (Claude Desktop, Continue, or similar)

### Configuration Options

Choose one of the following configuration methods based on your needs:

#### Option 1: Basic Docker Configuration

Add to your MCP settings:

```json
{
  "servers": {
    "helm-chart-mcp": {
      "command": "docker",
      "args": [
        "run",
        "--rm",
        "-i",
        "helm-chart-mcp:latest"
      ]
    }
  }
}
```

#### Option 2: With Environment Variables

For private OCI registries, use environment variables:

```json
{
  "servers": {
    "helm-chart-mcp": {
      "command": "docker",
      "args": [
        "run",
        "--rm",
        "-i",
        "-e",
        "HELM_OCI_REGISTRY=your-registry.com",
        "-e",
        "HELM_OCI_USERNAME=your-username",
        "-e",
        "HELM_OCI_PASSWORD=your-password-or-token",
        "helm-chart-mcp:latest"
      ]
    }
  }
}
```

#### Option 3: Using Environment File

For multiple environment variables, use an .env file:

```json
{
  "servers": {
    "helm-chart-mcp": {
      "command": "docker",
      "args": [
        "run",
        "--rm",
        "-i",
        "--env-file",
        "${workspaceFolder}/.env",
        "ghcr.io/v3ro/helm-chart-mcp:0.0.1"
      ]
    }
  }
}
```

Create a `.env` file in your workspace:
```
HELM_OCI_REGISTRY=your-registry.com
HELM_OCI_USERNAME=your-username
HELM_OCI_PASSWORD=your-password-or-token
```

### Build and Run

1. **Build the Docker image:**
   ```bash
   docker build -t helm-chart-mcp .
   ```

2. **Test the server:**
   ```bash
   docker run --rm -i helm-chart-mcp:latest
   ```

## Example Usage

The MCP server provides tools to:

1. **Analyze chart dependencies** from Chart.yaml content
2. **Download and cache dependencies** for offline analysis
3. **Get configuration options** for specific chart dependencies
4. **Validate values configurations** against dependency schemas

Perfect for scenarios like configuring applications with nginx sidecars, setting up complex multi-dependency charts, or understanding available configuration options for enterprise Helm charts.

## Tools Available

- `analyze_chart_dependencies`: Analyze dependencies from Chart.yaml
- `download_dependencies`: Download and cache chart dependencies
- `get_configuration_options`: Get config options for dependencies
- `validate_dependency_values`: Validate values against schemas

## Development

This MCP server is built with Python and uses the mcp library for Model Context Protocol integration. The server provides intelligent assistance for Helm chart configuration and dependency management.