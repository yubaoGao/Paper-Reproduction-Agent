"""Safe static dependency-manifest parsing; repository installers never execute."""

from __future__ import annotations

import tomllib

import yaml
from packaging.requirements import Requirement


class DependencyManifestError(ValueError):
    pass


class DependencyManifestParser:
    def requirements_txt(self, content: str) -> tuple[str, ...]:
        values = []
        for raw in content.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(("-r", "--requirement", "-c", "--constraint", "--")):
                raise DependencyManifestError(
                    "requirements indirection and installer options require prior analysis"
                )
            values.append(self._requirement(line))
        return tuple(values)

    def pyproject(self, content: bytes) -> tuple[str, ...]:
        try:
            value = tomllib.loads(content.decode("utf-8"))
        except Exception as exc:
            raise DependencyManifestError("invalid pyproject.toml") from exc
        dependencies = value.get("project", {}).get("dependencies", ())
        if not isinstance(dependencies, list):
            raise DependencyManifestError("project.dependencies must be a list")
        return tuple(self._requirement(str(item)) for item in dependencies)

    def environment_yml(self, content: str) -> tuple[str, ...]:
        try:
            value = yaml.safe_load(content) or {}
        except yaml.YAMLError as exc:
            raise DependencyManifestError("invalid environment.yml") from exc
        dependencies = value.get("dependencies", ())
        if not isinstance(dependencies, list):
            raise DependencyManifestError("environment dependencies must be a list")
        result = []
        for item in dependencies:
            if isinstance(item, str):
                normalized = item.replace("=", "==", 1) if "=" in item and "==" not in item else item
                result.append(self._requirement(normalized))
            elif isinstance(item, dict) and set(item) == {"pip"}:
                pip = item["pip"]
                if not isinstance(pip, list):
                    raise DependencyManifestError("pip dependencies must be a list")
                result.extend(self._requirement(str(value)) for value in pip)
            else:
                raise DependencyManifestError("unsupported environment dependency entry")
        return tuple(result)

    @staticmethod
    def _requirement(value: str) -> str:
        try:
            Requirement(value)
        except Exception as exc:
            raise DependencyManifestError(f"unsupported package requirement: {value!r}") from exc
        return value
