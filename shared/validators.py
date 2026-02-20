"""
Shared validators for DavyBot plugins.

This file is duplicated in services/agent-api/gewei/plugins/validators.py
for runtime use. Keep both files in sync.
"""

import re
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, validator, ValidationError


logger = logging.getLogger(__name__)


# Regex patterns
PLUGIN_NAME_PATTERN = re.compile(r'^[a-z0-9]([a-z0-9-]*[a-z0-9])?$')
VERSION_PATTERN = re.compile(r'^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$')


class DependencySpec(BaseModel):
    """Plugin dependency specification"""
    pypi: List[str] = Field(default_factory=list)
    system: List[str] = Field(default_factory=list)
    plugins: List[str] = Field(default_factory=list)


class ConfigProperty(BaseModel):
    """Configuration schema property"""
    type: str
    default: Optional[Any] = None
    description: Optional[str] = None
    enum: Optional[List[Any]] = None
    format: Optional[str] = None
    pattern: Optional[str] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None


class ConfigSchema(BaseModel):
    """Configuration schema"""
    type: str = "object"
    properties: Dict[str, ConfigProperty] = Field(default_factory=dict)
    required: List[str] = Field(default_factory=list)
    additional_properties: bool = True


class PluginSettings(BaseModel):
    """Plugin settings"""
    enabled: bool = True
    priority: int = 50
    auto_activate: bool = False
    timeout: float = 30.0
    max_retries: int = 3


class PluginManifest(BaseModel):
    """
    Plugin manifest validator for plugin.yaml files.

    This validates the structure and content of plugin.yaml files.
    """

    api_version: str = Field(..., pattern=r'^\d+\.\d+$')
    name: str = Field(..., min_length=1, max_length=100)
    version: str = Field(..., pattern=r'^\d+\.\d+\.\d+')
    description: str = Field(..., min_length=1, max_length=500)
    author: str = Field(..., min_length=1, max_length=100)
    license: str = Field(default="MIT", min_length=1, max_length=50)
    python_version: str = Field(default=">=3.12")

    plugin_type: str = Field(..., alias="type")
    entry_point: str = Field(..., pattern=r'^[a-zA-Z_][a-zA-Z0-9_.]*:[A-Z][a-zA-Z0-9_]*$')

    dependencies: Dict[str, List[str]] = Field(default_factory=dict)
    config_schema: Dict[str, Any] = Field(default_factory=dict)
    hooks: List[str] = Field(default_factory=list)
    settings: Dict[str, Any] = Field(default_factory=dict)

    @validator('name')
    def validate_name(cls, v):
        """Validate plugin name format"""
        if not PLUGIN_NAME_PATTERN.match(v):
            raise ValueError(
                f"Invalid plugin name '{v}'. "
                "Must be lowercase alphanumeric with hyphens, "
                "start and end with alphanumeric."
            )
        return v

    @validator('plugin_type')
    def validate_plugin_type(cls, v):
        """Validate plugin type"""
        valid_types = ['channel', 'tool', 'service', 'memory']
        if v not in valid_types:
            raise ValueError(
                f"Invalid plugin type '{v}'. "
                f"Must be one of: {', '.join(valid_types)}"
            )
        return v

    @validator('version')
    def validate_version(cls, v):
        """Validate semantic version"""
        if not VERSION_PATTERN.match(v):
            raise ValueError(
                f"Invalid version '{v}'. "
                "Must follow semantic versioning: MAJOR.MINOR.PATCH"
            )
        return v

    @validator('python_version')
    def validate_python_version(cls, v):
        """Validate Python version specifier"""
        if not v.startswith('>=') and not v.startswith('=='):
            raise ValueError(
                f"Invalid Python version '{v}'. "
                "Must start with '>=' or '=='"
            )
        return v

    @validator('entry_point')
    def validate_entry_point(cls, v):
        """Validate entry point format"""
        if ':' not in v:
            raise ValueError(
                f"Invalid entry point '{v}'. "
                "Must be in format 'module:ClassName'"
            )
        return v

    @validator('dependencies')
    def validate_dependencies(cls, v):
        """Validate dependencies structure"""
        valid_keys = ['pypi', 'system', 'plugins']
        for key in v:
            if key not in valid_keys:
                raise ValueError(
                    f"Invalid dependency type '{key}'. "
                    f"Must be one of: {', '.join(valid_keys)}"
                )

            if not isinstance(v[key], list):
                raise ValueError(
                    f"Dependencies '{key}' must be a list"
                )

        return v

    @validator('config_schema')
    def validate_config_schema(cls, v):
        """Validate configuration schema"""
        if not v:
            return v

        if 'type' not in v:
            raise ValueError("config_schema must have 'type' field")

        if v['type'] != 'object':
            raise ValueError("config_schema type must be 'object'")

        if 'properties' in v and not isinstance(v['properties'], dict):
            raise ValueError("config_schema 'properties' must be a dictionary")

        if 'required' in v and not isinstance(v['required'], list):
            raise ValueError("config_schema 'required' must be a list")

        return v

    @validator('hooks')
    def validate_hooks(cls, v):
        """Validate event hooks"""
        valid_hooks = [
            'before_tool_call',
            'after_tool_call',
            'on_task_start',
            'on_task_complete',
            'on_task_error',
            'on_agent_start',
            'on_agent_complete',
            'on_agent_error',
            'on_message_sent',
            'on_message_received',
            'on_error',
            'on_checkpoint',
        ]

        for hook in v:
            if hook not in valid_hooks:
                logger.warning(f"Unknown hook '{hook}'. Known hooks: {valid_hooks}")

        return v

    @validator('settings')
    def validate_settings(cls, v):
        """Validate plugin settings"""
        if 'enabled' in v and not isinstance(v['enabled'], bool):
            raise ValueError("setting 'enabled' must be a boolean")

        if 'priority' in v:
            if not isinstance(v['priority'], int):
                raise ValueError("setting 'priority' must be an integer")
            if v['priority'] < 0 or v['priority'] > 100:
                raise ValueError("setting 'priority' must be between 0 and 100")

        if 'auto_activate' in v and not isinstance(v['auto_activate'], bool):
            raise ValueError("setting 'auto_activate' must be a boolean")

        return v


class PluginConfigValidator:
    """
    Validate plugin configuration against schema.
    """

    @staticmethod
    def validate_config(config: Dict[str, Any], schema: Dict[str, Any]) -> bool:
        """
        Validate configuration against schema.

        Args:
            config: Configuration to validate
            schema: JSON schema

        Returns:
            True if valid

        Raises:
            ValueError: If configuration is invalid
        """
        if not schema:
            return True

        # Check required fields
        required = schema.get('required', [])
        for field in required:
            if field not in config:
                raise ValueError(f"Missing required field: {field}")

        # Check each property
        properties = schema.get('properties', {})
        for field, value in config.items():
            if field not in properties:
                if not schema.get('additional_properties', True):
                    raise ValueError(f"Unexpected field: {field}")
                continue

            prop_schema = properties[field]
            PluginConfigValidator._validate_property(field, value, prop_schema)

        return True

    @staticmethod
    def _validate_property(field: str, value: Any, schema: Dict[str, Any]) -> None:
        """Validate a single property against schema"""
        expected_type = schema.get('type')

        if expected_type == 'string':
            if not isinstance(value, str):
                raise ValueError(f"Field '{field}' must be a string")

            # Check min/max length
            if 'min_length' in schema and len(value) < schema['min_length']:
                raise ValueError(
                    f"Field '{field}' must be at least {schema['min_length']} characters"
                )
            if 'max_length' in schema and len(value) > schema['max_length']:
                raise ValueError(
                    f"Field '{field}' must be at most {schema['max_length']} characters"
                )

            # Check pattern
            if 'pattern' in schema:
                pattern = re.compile(schema['pattern'])
                if not pattern.match(value):
                    raise ValueError(
                        f"Field '{field}' does not match required pattern"
                    )

            # Check format
            if 'format' in schema:
                if schema['format'] == 'uri' and not value.startswith(('http://', 'https://')):
                    raise ValueError(f"Field '{field}' must be a valid URI")

            # Check enum
            if 'enum' in schema and value not in schema['enum']:
                raise ValueError(
                    f"Field '{field}' must be one of: {schema['enum']}"
                )

        elif expected_type == 'integer':
            if not isinstance(value, int):
                raise ValueError(f"Field '{field}' must be an integer")

            if 'minimum' in schema and value < schema['minimum']:
                raise ValueError(
                    f"Field '{field}' must be >= {schema['minimum']}"
                )
            if 'maximum' in schema and value > schema['maximum']:
                raise ValueError(
                    f"Field '{field}' must be <= {schema['maximum']}"
                )

        elif expected_type == 'number':
            if not isinstance(value, (int, float)):
                raise ValueError(f"Field '{field}' must be a number")

            if 'minimum' in schema and value < schema['minimum']:
                raise ValueError(
                    f"Field '{field}' must be >= {schema['minimum']}"
                )
            if 'maximum' in schema and value > schema['maximum']:
                raise ValueError(
                    f"Field '{field}' must be <= {schema['maximum']}"
                )

        elif expected_type == 'boolean':
            if not isinstance(value, bool):
                raise ValueError(f"Field '{field}' must be a boolean")

        elif expected_type == 'array':
            if not isinstance(value, list):
                raise ValueError(f"Field '{field}' must be an array")

        elif expected_type == 'object':
            if not isinstance(value, dict):
                raise ValueError(f"Field '{field}' must be an object")


def validate_plugin_directory(plugin_dir: Path) -> tuple[bool, List[str]]:
    """
    Validate plugin directory structure.

    Args:
        plugin_dir: Path to plugin directory

    Returns:
        Tuple of (is_valid, error_messages)
    """
    errors = []

    # Check directory exists
    if not plugin_dir.exists():
        errors.append(f"Plugin directory does not exist: {plugin_dir}")
        return False, errors

    # Check required files
    required_files = ['plugin.yaml', 'plugin.py']
    for filename in required_files:
        file_path = plugin_dir / filename
        if not file_path.exists():
            errors.append(f"Missing required file: {filename}")

    # Check README exists (recommended)
    readme_path = plugin_dir / 'README.md'
    if not readme_path.exists():
        errors.append(f"Missing recommended file: README.md")

    # Validate plugin.yaml
    yaml_path = plugin_dir / 'plugin.yaml'
    if yaml_path.exists():
        try:
            from shared.utils import load_yaml_file
            manifest_data = load_yaml_file(yaml_path)
            PluginManifest(**manifest_data)
        except ValidationError as e:
            errors.append(f"Invalid plugin.yaml: {e}")
        except Exception as e:
            errors.append(f"Error loading plugin.yaml: {e}")

    # Check tests directory
    tests_dir = plugin_dir / 'tests'
    if not tests_dir.exists():
        errors.append(f"Missing tests directory (recommended)")

    return len(errors) == 0, errors


def validate_plugin_yaml(yaml_path: Path) -> tuple[bool, PluginManifest, List[str]]:
    """
    Validate plugin.yaml file.

    Args:
        yaml_path: Path to plugin.yaml

    Returns:
        Tuple of (is_valid, manifest, error_messages)
    """
    errors = []

    if not yaml_path.exists():
        errors.append(f"plugin.yaml does not exist: {yaml_path}")
        return False, None, errors

    try:
        from shared.utils import load_yaml_file
        manifest_data = load_yaml_file(yaml_path)
        manifest = PluginManifest(**manifest_data)
        return True, manifest, []
    except ValidationError as e:
        errors.append(f"Validation error: {e}")
        return False, None, errors
    except Exception as e:
        errors.append(f"Error loading plugin.yaml: {e}")
        return False, None, errors
