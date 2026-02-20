"""
Base classes for DavyBot plugins.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Any, Optional
from enum import Enum


class PluginState(str, Enum):
    """Plugin states"""
    UNINITIALIZED = "uninitialized"
    INITIALIZED = "initialized"
    ACTIVATED = "activated"
    DEACTIVATED = "deactivated"
    ERROR = "error"


@dataclass
class PluginConfig:
    """Plugin configuration"""
    name: str
    version: str
    enabled: bool = True
    settings: Dict[str, Any] = None

    def __post_init__(self):
        if self.settings is None:
            self.settings = {}


class BasePlugin(ABC):
    """Base plugin class"""

    def __init__(self, config: PluginConfig):
        self.config = config
        self.state = PluginState.UNINITIALIZED
        self.is_activated = False

    async def initialize(self) -> None:
        """Initialize the plugin"""
        self.state = PluginState.INITIALIZED

    async def activate(self) -> None:
        """Activate the plugin"""
        self.state = PluginState.ACTIVATED
        self.is_activated = True

    async def deactivate(self) -> None:
        """Deactivate the plugin"""
        self.state = PluginState.DEACTIVATED
        self.is_activated = False

    async def cleanup(self) -> None:
        """Cleanup resources"""
        pass


class ChannelPlugin(BasePlugin):
    """Base class for channel plugins (Slack, Feishu, etc.)"""

    async def send_message(self, message: str, **kwargs) -> bool:
        """Send a text message"""
        raise NotImplementedError

    async def send_rich_message(self, content: Dict[str, Any], **kwargs) -> bool:
        """Send a rich message (card, embed, etc.)"""
        raise NotImplementedError

    def register_hooks(self) -> Dict[str, Any]:
        """Register event hooks"""
        return {}
