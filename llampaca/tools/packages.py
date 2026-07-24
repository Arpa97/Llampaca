"""
Package installer tool: allows the agent to propose installing a missing Python or Node package.
Registered with requires_confirmation=True so the user MUST explicitly approve every installation.
"""

from llampaca import packages
from llampaca.tools.registry import ToolRegistry


def register_package_tools(registry: ToolRegistry) -> None:
    """Register the install_package tool on the given ToolRegistry."""

    def install_package(package_name: str, ecosystem: str = "python") -> str:
        """
        Install a missing Python (venv) or Node.js (~/.llampaca/node_modules) package.
        Requires explicit user confirmation before executing.

        Args:
            package_name: Name of the package to install (e.g. 'docx', 'requests', 'express').
            ecosystem: 'python' (installed in venv) or 'node' (installed in ~/.llampaca/node_modules).
        """
        eco = ecosystem.strip().lower()
        try:
            if eco in ("python", "py", "pip"):
                return packages.install_python_package(package_name)
            elif eco in ("node", "npm", "js"):
                return packages.install_node_package(package_name)
            else:
                return f"Error: unsupported ecosystem '{ecosystem}'. Use 'python' or 'node'."
        except Exception as e:
            return f"Error installing package '{package_name}': {e}"

    # Always gated behind user confirmation
    registry.register(install_package, requires_confirmation=True)
