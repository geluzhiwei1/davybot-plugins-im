"""
Shared utility functions for DavyBot plugins.

This file is duplicated in services/agent-api/gewei/plugins/utils.py
for runtime use. Keep both files in sync.
"""

import asyncio
import hashlib
import json
import os
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, TypeVar
from datetime import datetime

import yaml


# Configure logging
logger = logging.getLogger(__name__)


T = TypeVar('T')


def load_yaml_file(file_path: Path) -> Dict[str, Any]:
    """
    Load YAML file safely.

    Args:
        file_path: Path to YAML file

    Returns:
        Parsed YAML content as dictionary

    Raises:
        FileNotFoundError: If file doesn't exist
        yaml.YAMLError: If YAML is invalid
    """
    if not file_path.exists():
        raise FileNotFoundError(f"YAML file not found: {file_path}")

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        logger.error(f"Invalid YAML in {file_path}: {e}")
        raise


def save_yaml_file(file_path: Path, data: Dict[str, Any]) -> None:
    """
    Save data to YAML file safely.

    Args:
        file_path: Path to save YAML file
        data: Data to save
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with open(file_path, 'w', encoding='utf-8') as f:
        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)


def load_json_file(file_path: Path) -> Dict[str, Any]:
    """
    Load JSON file safely.

    Args:
        file_path: Path to JSON file

    Returns:
        Parsed JSON content as dictionary

    Raises:
        FileNotFoundError: If file doesn't exist
        json.JSONDecodeError: If JSON is invalid
    """
    if not file_path.exists():
        raise FileNotFoundError(f"JSON file not found: {file_path}")

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {file_path}: {e}")
        raise


def save_json_file(file_path: Path, data: Dict[str, Any], indent: int = 2) -> None:
    """
    Save data to JSON file safely.

    Args:
        file_path: Path to save JSON file
        data: Data to save
        indent: JSON indentation (default: 2)
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)


def compute_file_hash(file_path: Path, algorithm: str = "sha256") -> str:
    """
    Compute hash of a file.

    Args:
        file_path: Path to file
        algorithm: Hash algorithm (default: sha256)

    Returns:
        Hex digest of file hash
    """
    hash_obj = hashlib.new(algorithm)

    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b''):
            hash_obj.update(chunk)

    return hash_obj.hexdigest()


def compute_string_hash(text: str, algorithm: str = "sha256") -> str:
    """
    Compute hash of a string.

    Args:
        text: Text to hash
        algorithm: Hash algorithm (default: sha256)

    Returns:
        Hex digest of string hash
    """
    hash_obj = hashlib.new(algorithm)
    hash_obj.update(text.encode('utf-8'))
    return hash_obj.hexdigest()


def ensure_directory(path: Path) -> Path:
    """
    Ensure directory exists, create if it doesn't.

    Args:
        path: Directory path

    Returns:
        Path object
    """
    path.mkdir(parents=True, exist_ok=True)
    return path


def find_files_by_extension(
    directory: Path,
    extensions: List[str],
    recursive: bool = True
) -> List[Path]:
    """
    Find files by extension in directory.

    Args:
        directory: Directory to search
        extensions: List of file extensions (e.g., ['.py', '.yaml'])
        recursive: Search recursively

    Returns:
        List of file paths
    """
    files = []

    if recursive:
        for ext in extensions:
            files.extend(directory.rglob(f"*{ext}"))
    else:
        for ext in extensions:
            files.extend(directory.glob(f"*{ext}"))

    return sorted(files)


def validate_python_class_name(name: str) -> bool:
    """
    Validate Python class name.

    Args:
        name: Class name to validate

    Returns:
        True if valid class name
    """
    if not name:
        return False

    # Must start with uppercase letter or underscore
    if not (name[0].isupper() or name[0] == '_'):
        return False

    # Rest must be alphanumeric or underscore
    return all(c.isalnum() or c == '_' for c in name)


def parse_entry_point(entry_point: str) -> tuple[str, str]:
    """
    Parse plugin entry point string.

    Args:
        entry_point: Entry point in format "module:ClassName" or "module.submodule:ClassName"

    Returns:
        Tuple of (module_name, class_name)

    Raises:
        ValueError: If entry point format is invalid

    Examples:
        >>> parse_entry_point("plugin:MyPlugin")
        ('plugin', 'MyPlugin')
        >>> parse_entry_point("my.sub.plugin:MyPlugin")
        ('my.sub.plugin', 'MyPlugin')
    """
    if ':' not in entry_point:
        raise ValueError(
            f"Invalid entry point format: {entry_point}. "
            "Expected 'module:ClassName'"
        )

    module_name, class_name = entry_point.split(':', 1)

    if not module_name or not class_name:
        raise ValueError(
            f"Invalid entry point: {entry_point}. "
            "Module and class name must not be empty"
        )

    if not validate_python_class_name(class_name):
        raise ValueError(
            f"Invalid class name in entry point: {class_name}. "
            "Class names must start with uppercase letter"
        )

    return module_name, class_name


def import_class(module_name: str, class_name: str) -> Type[T]:
    """
    Dynamically import a class from a module.

    Args:
        module_name: Module path (e.g., 'my.module')
        class_name: Class name (e.g., 'MyClass')

    Returns:
        Imported class

    Raises:
        ImportError: If module or class cannot be imported
    """
    try:
        import importlib
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)
        return cls
    except ImportError as e:
        raise ImportError(
            f"Failed to import module '{module_name}': {e}"
        )
    except AttributeError as e:
        raise ImportError(
            f"Class '{class_name}' not found in module '{module_name}': {e}"
        )


def format_timestamp(dt: Optional[datetime] = None) -> str:
    """
    Format datetime as ISO string.

    Args:
        dt: Datetime object (default: current time)

    Returns:
        ISO formatted timestamp
    """
    if dt is None:
        dt = datetime.now()
    return dt.isoformat()


def parse_timestamp(timestamp: str) -> datetime:
    """
    Parse ISO timestamp string.

    Args:
        timestamp: ISO formatted timestamp

    Returns:
        Datetime object
    """
    return datetime.fromisoformat(timestamp)


def truncate_string(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """
    Truncate string to max length.

    Args:
        text: Text to truncate
        max_length: Maximum length
        suffix: Suffix to add if truncated

    Returns:
        Truncated string
    """
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


def safe_get(dictionary: Dict[str, Any], key: str, default: Any = None) -> Any:
    """
    Safely get value from dictionary with default.

    Args:
        dictionary: Dictionary to get value from
        key: Key to look up
        default: Default value if key not found

    Returns:
        Value or default
    """
    return dictionary.get(key, default)


def merge_dicts(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recursively merge two dictionaries.

    Args:
        base: Base dictionary
        override: Override dictionary (takes precedence)

    Returns:
        Merged dictionary
    """
    result = base.copy()

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_dicts(result[key], value)
        else:
            result[key] = value

    return result


async def run_with_timeout(
    coro,
    timeout: float,
    default: Any = None
) -> Any:
    """
    Run coroutine with timeout.

    Args:
        coro: Coroutine to run
        timeout: Timeout in seconds
        default: Default value if timeout occurs

    Returns:
        Coroutine result or default value
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(f"Coroutine timed out after {timeout}s")
        return default


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename by removing invalid characters.

    Args:
        filename: Original filename

    Returns:
        Sanitized filename
    """
    # Remove invalid characters
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, '_')

    # Remove leading/trailing spaces and dots
    filename = filename.strip('. ')

    # Ensure filename is not empty
    if not filename:
        filename = "unnamed"

    return filename


def get_file_size_mb(file_path: Path) -> float:
    """
    Get file size in megabytes.

    Args:
        file_path: Path to file

    Returns:
        File size in MB
    """
    if not file_path.exists():
        return 0.0

    return file_path.stat().st_size / (1024 * 1024)


def format_file_size(size_bytes: int) -> str:
    """
    Format file size in human-readable format.

    Args:
        size_bytes: Size in bytes

    Returns:
        Formatted size string (e.g., "1.5 MB")
    """
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


def create_plugin_id(name: str, version: str) -> str:
    """
    Create unique plugin ID from name and version.

    Args:
        name: Plugin name
        version: Plugin version

    Returns:
        Unique plugin ID
    """
    return f"{name}@{version}"
