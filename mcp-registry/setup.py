"""Setup script for MCP Registry."""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as fh:
    requirements = [line.strip() for line in fh if line.strip() and not line.startswith("#")]

setup(
    name="mcp-registry",
    version="1.0.0",
    author="MCP Registry Team",
    author_email="registry@modelcontextprotocol.io",
    description="Centralized registry for Model Context Protocol servers",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/samscarrow/mcp-registry",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    python_requires=">=3.8",
    install_requires=requirements,
    extras_require={
        "dev": [
            "pytest>=7.4.0",
            "pytest-asyncio>=0.21.0",
            "pytest-cov>=4.1.0",
            "black>=23.9.0",
            "flake8>=6.0.0",
            "mypy>=1.6.0",
            "pre-commit>=3.5.0",
            "httpx>=0.25.0",  # For testing
        ]
    },
    entry_points={
        "console_scripts": [
            "mcp-registry=mcp_registry.api.server:main",
        ],
    },
    include_package_data=True,
    package_data={
        "mcp_registry": ["web/*.html"],
    },
)