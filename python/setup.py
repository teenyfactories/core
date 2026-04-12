"""
Setup file for backward compatibility with older pip versions.
Modern installation should use pyproject.toml.
"""

from setuptools import setup

# Read version from __version__.py
version = {}
with open("teenyfactories/__version__.py") as f:
    exec(f.read(), version)

setup(
    name="teenyfactories",
    version=version['__version__'],
    description="Multi-provider LLM and message queue abstraction for distributed agent systems",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="TeenyFactories Team",
    license="MIT",
    packages=["teenyfactories"],
    python_requires=">=3.9",
)
