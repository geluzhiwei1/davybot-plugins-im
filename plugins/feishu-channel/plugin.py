"""
Feishu (Lark) Channel Plugin for DavyBot Agent Platform

Using lark-oapi SDK 1.5.3+ for full Feishu API support:
- App credentials (app_id/app_secret) - Recommended for production
- Text messages and interactive cards
- Image and file uploads
- Message editing
- @Mention support
- Rich card templates
- Retry logic with exponential backoff

Compatible with lark-oapi >= 1.5.3

Reference: deps/openclaw-china/extensions/feishu
"""

import logging
import asyncio
import threading
import json
import time
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from concurrent import futures
from pathlib import Path
import sys

import aiohttp
from aiohttp import web
from datetime import datetime

from lark_oapi import Client
from lark_oapi.ws import Client as WsClient
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    Image,
    CreateImageRequest,
    CreateImageRequestBody,
)

# Try multiple possible paths for shared module
possible_shared_paths = [
    Path(__file__).parent,
    Path(__file__).parent.parent / "shared",
]

for shared_path in possible_shared_paths:
    if shared_path.exists():
        if str(shared_path) not in sys.path:
            sys.path.insert(0, str(shared_path))
    

from plugin_base import ChannelPlugin, PluginConfig


logger = logging.getLogger(__name__)


def async_retry(max_retries: int, delay: float):
    """
    Decorator for retrying async functions on failure with exponential backoff.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < max_retries:
                        wait_time = delay * (2 ** attempt)
                        logger.warning(f"Attempt {attempt + 1} failed: {e}, retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
            raise last_error
        return wrapper
    return decorator


class RenderMode(str, Enum):
    """Message render mode"""
    AUTO = "auto"
    RAW = "raw"
    CARD = "card"


class ReceiveIdType(str, Enum):
    """Receive ID type for Feishu messages"""
    CHAT_ID = "chat_id"
    OPEN_ID = "open_id"
    USER_ID = "user_id"
    UNION_ID = "union_id"


@dataclass
class FeishuMessageResult:
    """Result of sending a Feishu message"""
    message_id: str
    chat_id: str


class FeishuChannelPlugin(ChannelPlugin):
    """
    Feishu channel plugin using lark-oapi SDK 1.5.3+.

    Authentication:
    - app_id + app_secret - Full API access
      * Text and card messages
      * Image/file upload
      * Message editing
      * @Mention support

    Features:
    - Text and interactive card messages
    - Image/file upload
    - @Mention support
    - Rich card templates (success, error, warning)
    - Retry logic with exponential backoff
    """

    version = "0.2.0"
    author = "DavyBot Team"
    description = "Feishu (Lark) channel integration using lark-oapi SDK 1.5.3+"

    def __init__(self, config: PluginConfig):
        super().__init__(config)

        # === FAST FAIL: Validate configuration ===
        app_id = config.settings.get("app_id")
        app_secret = config.settings.get("app_secret")
        receive_id = config.settings.get("receive_id")

        if not app_id or not app_secret:
            raise ValueError(
                "Feishu plugin requires app_id and app_secret:\n"
                "  - app_id: 飞书应用 ID\n"
                "  - app_secret: 飞书应用密钥\n"
                "请到飞书开放平台创建应用并获取凭证：https://open.feishu.cn"
            )

        # Store configuration
        self.app_id = app_id
        self.app_secret = app_secret
        self.receive_id = receive_id or ""

        # Lark client (initialized in async initialize)
        self._client: Optional[Client] = None

        # Receive ID type
        receive_id_type = config.settings.get("receive_id_type", "chat_id")
        try:
            self._receive_id_type = ReceiveIdType(receive_id_type)
        except ValueError:
            self._receive_id_type = ReceiveIdType.CHAT_ID

        # Message settings
        self.enable_on_tool_call = config.settings.get("enable_on_tool_call", False)
        self.enable_on_task_complete = config.settings.get("enable_on_task_complete", True)
        self.enable_on_error = config.settings.get("enable_on_error", True)

        # Render mode
        render_mode = config.settings.get("render_mode", "auto")
        self.render_mode = RenderMode(render_mode)

        # Retry settings
        self.max_retries = config.settings.get("max_retries", 3)
        self.retry_delay = config.settings.get("retry_delay", 1.0)

        # === Event receiving configuration ===
        event_config = config.settings.get("event_config", {})
        self.event_mode = event_config.get("event_mode", "long_connection")
        self.encrypt_key = event_config.get("encrypt_key", "")

        # HTTP callback settings
        self.http_port = event_config.get("http_port", 8466)
        self.http_host = event_config.get("http_host", "0.0.0.0")

        # Event handler (will be initialized in activate())
        self._long_connection_handler = None
        self._http_app = None
        self._http_runner = None
        self._event_thread = None

        # For async message handling in long connection mode
        self._message_queue: asyncio.Queue | None = None
        self._message_worker_task: asyncio.Task | None = None

        logger.info(f"Feishu plugin configured with event_mode={self.event_mode}")

    async def initialize(self) -> None:
        """Initialize plugin with lark-oapi client and start event receiver."""
        # Initialize lark-oapi client with Builder pattern (SDK 1.5.3+)
        self._client = Client.builder() \
            .app_id(self.app_id) \
            .app_secret(self.app_secret) \
            .build()

        logger.info(f"Feishu plugin initialized with app_id={self.app_id}")
        # Note: SDK 1.5.3+ handles authentication automatically

        # Auto-start event receiver during initialization
        # This ensures the plugin can receive messages even without explicit activation
        if self.event_mode == "long_connection":
            logger.info("🚀 Auto-starting Feishu long connection during initialization...")
            try:
                await self._start_long_connection()
                logger.info("✅ Feishu long connection auto-started successfully")
            except Exception as e:
                logger.error(f"❌ Failed to auto-start long connection: {e}", exc_info=True)
                # Don't fail initialization if long connection fails
                # It might be a temporary issue
        elif self.event_mode == "http_callback":
            logger.info("🚀 Auto-starting Feishu HTTP callback server during initialization...")
            try:
                await self._start_http_callback()
                logger.info("✅ Feishu HTTP callback server auto-started successfully")
            except Exception as e:
                logger.error(f"❌ Failed to auto-start HTTP callback server: {e}", exc_info=True)

    async def activate(self) -> None:
        """Activate plugin (event receiver already started in initialize)."""
        # Event receiver is already started in initialize()
        # Just call parent to set the activated flag
        await super().activate()
        logger.info("Feishu plugin activated (event receiver already running)")

    async def deactivate(self) -> None:
        """Deactivate plugin and stop event receiver."""
        # Stop event receiver
        logger.info("Stopping event receiver...")
        if self.event_mode == "long_connection":
            await self._stop_long_connection()
        elif self.event_mode == "http_callback":
            await self._stop_http_callback()

        await super().deactivate()
        logger.info("Feishu plugin deactivated")

    # ========================================================================
    # Message Sending
    # ========================================================================

    @async_retry(max_retries=3, delay=1.0)
    async def send_message(self, message: str, **kwargs) -> bool:
        """
        Send a text message.

        Args:
            message: Text message to send
            **kwargs: Optional receive_id, receive_id_type

        Returns:
            True if successful
        """
        # 激活状态由配置文件的 enabled/activated 决定，不再检查 self.is_activated
        receive_id = kwargs.get("receive_id", self.receive_id)
        if not receive_id:
            raise ValueError("receive_id is required")

        receive_id_type = kwargs.get("receive_id_type", self._receive_id_type.value)

        # 使用 App API 发送文本消息
        return await self._send_via_api(
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            msg_type="text",
            content={"text": message}
        )

    @async_retry(max_retries=3, delay=1.0)
    async def send_rich_message(self, content: Dict[str, Any], **kwargs) -> bool:
        """
        Send a rich interactive card message.

        Args:
            content: Card content dictionary with 'card' key
            **kwargs: Optional receive_id, receive_id_type

        Returns:
            True if successful
        """
        # 激活状态由配置文件的 enabled/activated 决定，不再检查 self.is_activated
        receive_id = kwargs.get("receive_id", self.receive_id)
        if not receive_id:
            raise ValueError("receive_id is required")

        receive_id_type = kwargs.get("receive_id_type", self._receive_id_type.value)

        card = content.get("card")
        if not card:
            raise ValueError("Card content is required")

        # 使用 App API 发送卡片消息
        return await self._send_via_api(
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            msg_type="interactive",
            content=card
        )

    async def send_card(
        self,
        title: str,
        content: str,
        template: str = "green",
        **kwargs
    ) -> bool:
        """
        Send a formatted card message.

        Args:
            title: Card title
            content: Card content
            template: Color template (green, red, blue, orange)
            **kwargs: Optional receive_id, receive_id_type

        Returns:
            True if successful
        """
        card = self._create_card(title, content, template)
        return await self.send_rich_message({"card": card}, **kwargs)

    @async_retry(max_retries=3, delay=1.0)
    async def send_image(self, image_key: str, **kwargs) -> bool:
        """
        Send an image message.

        Args:
            image_key: Feishu image key (img_vxxx) or URL/path to upload
            **kwargs: Optional receive_id, receive_id_type

        Returns:
            True if successful
        """
        # 激活状态由配置文件的 enabled/activated 决定，不再检查 self.is_activated

        receive_id = kwargs.get("receive_id", self.receive_id)
        if not receive_id:
            raise ValueError("receive_id is required")

        receive_id_type = kwargs.get("receive_id_type", self._receive_id_type.value)

        # If image_key doesn't look like a Feishu image key, upload it first
        if not image_key.startswith("img_"):
            image_key = await self._upload_image_from_url(image_key)

        return await self._send_via_api(
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            msg_type="image",
            content={"image_key": image_key}
        )

    # ========================================================================
    # Card Templates
    # ========================================================================

    def _create_card(
        self,
        title: str,
        content: str,
        template: str = "green"
    ) -> Dict[str, Any]:
        """
        Create a standard Feishu card.

        Args:
            title: Card title
            content: Card content
            template: Color template

        Returns:
            Card dictionary
        """
        return {
            "config": {
                "wide_screen_mode": True
            },
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": title
                },
                "template": template
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "plain_text",
                        "content": content
                    }
                }
            ]
        }

    def create_success_card(self, title: str, content: str) -> Dict[str, Any]:
        """Create a success card (green)."""
        return self._create_card(title, content, "green")

    def create_error_card(self, title: str, content: str) -> Dict[str, Any]:
        """Create an error card (red)."""
        return self._create_card(title, content, "red")

    def create_warning_card(self, title: str, content: str) -> Dict[str, Any]:
        """Create a warning card (orange)."""
        return self._create_card(title, content, "orange")

    # ========================================================================
    # Event Hooks
    # ========================================================================

    def register_hooks(self) -> Dict[str, Any]:
        """Register event hooks."""
        hooks = {}

        if self.enable_on_tool_call:
            hooks["after_tool_call"] = self._on_tool_call

        if self.enable_on_task_complete:
            hooks["on_task_complete"] = self._on_task_complete

        if self.enable_on_error:
            hooks["on_error"] = self._on_error

        return hooks

    async def _on_tool_call(self, event) -> None:
        """Handle tool execution event."""
        tool_name = getattr(event, "tool_name", "unknown")
        await self.send_message(f"🔧 Tool executed: `{tool_name}`")

    async def _on_task_complete(self, event) -> None:
        """Handle task completion event."""
        task_name = getattr(event, "task_name", "Task")
        result = getattr(event, "result", "Completed successfully")

        await self.send_card(
            title=f"✅ {task_name} Completed",
            content=str(result)[:2000],
            template="green"
        )

    async def _on_error(self, event) -> None:
        """Handle error event."""
        error_message = getattr(event, "error", "Unknown error")
        error_type = getattr(event, "error_type", "Error")

        await self.send_card(
            title=f"❌ {error_type}",
            content=str(error_message)[:2000],
            template="red"
        )

    # ========================================================================
    # Internal Methods
    # ========================================================================

    async def _send_via_api(
        self,
        receive_id: str,
        receive_id_type: str,
        msg_type: str,
        content: Dict[str, Any]
    ) -> bool:
        """Send message via lark-oapi SDK 1.5.3+ API."""
        if not self._client:
            raise RuntimeError("Feishu client not initialized")

        try:
            # Convert content to JSON string for non-interactive messages
            if msg_type == "interactive":
                content_json = content
            else:
                content_json = str(content).replace("'", '"')

            # Build request using Builder pattern (SDK 1.5.3+)
            request = CreateMessageRequest.builder() \
                .receive_id_type(receive_id_type) \
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(receive_id)
                    .msg_type(msg_type)
                    .content(content_json)
                    .build()
                ) \
                .build()

            # Send message via im.v1.message.create API
            response = self._client.im.v1.message.create(request)

            # Check response
            if response.code != 0:
                raise RuntimeError(f"Feishu API error: code={response.code}, msg={response.msg}")

            logger.info(f"Message sent successfully to {receive_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to send message via API: {e}")
            raise


    async def _upload_image_from_url(self, url: str) -> str:
        """Upload image from URL and return image key."""
        if not self._client:
            raise RuntimeError("Feishu client not initialized")

        try:
            # Download image
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"Failed to download image: {resp.status}")
                    image_data = await resp.read()

            # Upload to Feishu using CreateImageRequest (SDK 1.5.3+)
            request = CreateImageRequest.builder() \
                .request_body(
                    CreateImageRequestBody.builder()
                    .image_type("message")
                    .image(image_data)
                    .build()
                ) \
                .build()

            response = self._client.im.v1.image.create(request)

            if response.code != 0:
                raise RuntimeError(f"Failed to upload image: code={response.code}, msg={response.msg}")

            # Extract image_key from response data
            if not response.data or not response.data.image_key:
                raise RuntimeError("Upload response missing image_key")

            return response.data.image_key

        except Exception as e:
            logger.error(f"Failed to upload image: {e}")
            raise

    # ========================================================================
    # Event Receiving - Long Connection Mode
    # ========================================================================

    async def _start_long_connection(self) -> None:
        """Start long connection WebSocket client to receive Feishu events."""
        try:
            logger.info("🚀 Starting Feishu long connection...")

            # Create message queue for async processing
            self._message_queue = asyncio.Queue()

            # Start message worker in background
            self._message_worker_task = asyncio.create_task(self._message_worker())

            # Create a sync wrapper for the async callback
            # The long connection handler uses sync callback
            def sync_callback(event_data: Dict[str, Any]) -> None:
                """Sync wrapper to put messages into async queue."""
                try:
                    # Put message into queue (non-blocking)
                    self._message_queue.put_nowait(event_data)
                except Exception as e:
                    logger.error(f"Failed to queue message: {e}")

            # Create event handler instance with sync callback
            self._long_connection_handler = FeishuLongConnectionHandler(
                app_id=self.app_id,
                app_secret=self.app_secret,
                encrypt_key=self.encrypt_key or None,
                message_callback=sync_callback,
            )

            # Start in a separate thread (SDK handles event loop internally)
            self._event_thread = threading.Thread(
                target=self._long_connection_handler.start,
                daemon=True
            )
            self._event_thread.start()

            # Give it a moment to start
            await asyncio.sleep(0.5)

            logger.info("✅ Feishu long connection started in background thread")

        except Exception as e:
            logger.error(f"❌ Failed to start long connection: {e}", exc_info=True)
            raise

    async def _message_worker(self) -> None:
        """Background worker to process messages from queue."""
        logger.info("📥 Message worker started")
        while True:
            try:
                # Wait for message from queue
                event_data = await self._message_queue.get()

                # Process message asynchronously
                await self._on_message_received(event_data)

            except asyncio.CancelledError:
                logger.info("📥 Message worker cancelled")
                break
            except Exception as e:
                logger.error(f"❌ Error in message worker: {e}", exc_info=True)
                # Continue processing other messages
                await asyncio.sleep(0.1)

    async def _stop_long_connection(self) -> None:
        """Stop long connection WebSocket client."""
        try:
            # Stop message worker first
            if self._message_worker_task:
                logger.info("Stopping message worker...")
                self._message_worker_task.cancel()
                try:
                    await self._message_worker_task
                except asyncio.CancelledError:
                    pass
                self._message_worker_task = None

            if self._long_connection_handler:
                logger.info("Stopping Feishu long connection...")
                self._long_connection_handler.stop()
                self._long_connection_handler = None
                logger.info("Long connection stopped")
        except Exception as e:
            logger.error(f"Error stopping long connection: {e}", exc_info=True)

    # ========================================================================
    # Event Receiving - HTTP Callback Mode
    # ========================================================================

    async def _start_http_callback(self) -> None:
        """Start HTTP server to receive Feishu callback events."""
        try:

            logger.info(f"🚀 Starting Feishu HTTP callback server on {self.http_host}:{self.http_port}...")

            # Create aiohttp application
            self._http_app = web.Application()

            # Add routes
            self._http_app.router.add_post('/feishu/events', self._handle_http_event)
            self._http_app.router.add_get('/feishu/health', self._handle_http_health)

            # Create runner
            self._http_runner = web.AppRunner(self._http_app)
            await self._http_runner.setup()

            # Create TCP site
            site = web.TCPSite(self._http_runner, self.http_host, self.http_port)
            await site.start()

            logger.info(f"✅ Feishu HTTP callback server started: http://{self.http_host}:{self.http_port}")

        except Exception as e:
            logger.error(f"❌ Failed to start HTTP callback server: {e}", exc_info=True)
            raise

    async def _stop_http_callback(self) -> None:
        """Stop HTTP callback server."""
        try:
            if self._http_runner:
                logger.info("🛑 Stopping Feishu HTTP callback server...")
                await self._http_runner.cleanup()
                self._http_runner = None
                self._http_app = None
                logger.info("✅ HTTP callback server stopped")
        except Exception as e:
            logger.error(f"Error stopping HTTP callback server: {e}", exc_info=True)

    async def _handle_http_event(self, request: web.Request) -> web.Response:
        """Handle incoming Feishu HTTP callback events."""
        try:
            # Read request body
            body_bytes = await request.read()
            body_text = body_bytes.decode('utf-8')

            logger.debug(f"Received Feishu HTTP callback: {body_text[:200]}")

            # Parse JSON
            try:
                data = json.loads(body_text) if body_text else {}
            except json.JSONDecodeError:
                return web.Response(status=400, text="Invalid JSON")

            # Handle URL verification
            if data.get("type") == "url_verification":
                challenge = data.get("challenge")
                logger.info(f"URL verification challenge: {challenge}")
                return web.json_response({"challenge": challenge})

            # Handle encrypted events
            encrypt_key = data.get("encrypt")
            if encrypt_key:
                logger.warning("Encrypted events not implemented yet")
                return web.json_response({"code": 0, "msg": "success"})

            # Handle message events
            event_type = data.get("header", {}).get("event_type")
            if event_type == "im.message.receive_v1":
                await self._handle_http_message_event(data)

            return web.json_response({"code": 0, "msg": "success"})

        except Exception as e:
            logger.error(f"Error handling HTTP event: {e}", exc_info=True)
            return web.Response(status=500, text="Internal Error")

    async def _handle_http_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({
            "status": "ok",
            "service": "feishu-plugin-http-callback",
            "timestamp": time.time()
        })

    async def _handle_http_message_event(self, data: Dict[str, Any]) -> None:
        """Handle message event from HTTP callback."""
        try:
            event = data.get("event", {})
            message = event.get("message", {})
            sender = event.get("sender", {})

            # Extract message info
            chat_id = message.get("chat_id")
            message_id = message.get("message_id")
            message_type = message.get("message_type")

            # Parse content (JSON string)
            content_str = message.get("content", "{}")
            try:
                content = json.loads(content_str)
                text = content.get("text", "")
            except json.JSONDecodeError:
                text = content_str

            # Extract sender info
            sender_id = sender.get("sender_id", {})
            open_id = sender_id.get("open_id", "")
            sender_type = sender.get("sender_type")

            # Create event data
            event_data = {
                "event_type": "im.message.receive_v1",
                "chat_id": chat_id,
                "message_id": message_id,
                "message_type": message_type,
                "text": text,
                "sender_open_id": open_id,
                "sender_type": sender_type,
                "timestamp": time.time(),
            }

            logger.info(f"📩 HTTP callback received message: {text}")

            # Process message
            await self._on_message_received(event_data)

        except Exception as e:
            logger.error(f"Error handling HTTP message event: {e}", exc_info=True)

    # ========================================================================
    # Message Event Processing
    # ========================================================================

    async def _on_message_received(self, event_data: Dict[str, Any]) -> None:
        """
        Handle received Feishu message event.

        This method processes incoming messages by:
        1. Creating a UserInputMessage
        2. Creating and initializing an Agent
        3. Calling agent.process_message() to handle the message
        """
        try:
            # Extract message details
            event_type = event_data.get("event_type")
            chat_id = event_data.get("chat_id")
            message_id = event_data.get("message_id")
            message_type = event_data.get("message_type")
            text = event_data.get("text", "")
            sender_open_id = event_data.get("sender_open_id", "")
            sender_type = event_data.get("sender_type", "user")

            logger.info("=" * 60)
            logger.info("📩 收到飞书消息事件")
            logger.info("=" * 60)
            logger.info(f"   事件类型: {event_type}")
            logger.info(f"   聊天ID: {chat_id}")
            logger.info(f"   消息ID: {message_id}")
            logger.info(f"   消息类型: {message_type}")
            logger.info(f"   发送者: {sender_open_id} ({sender_type})")
            logger.info(f"   消息内容: {text}")
            logger.info("=" * 60)

            # Check if message mentions the bot (for @ mentions)
            # Remove @ mention from text
            if text and f"@{self.app_id}" in text:
                text = text.replace(f"@{self.app_id}", "").strip()
                logger.info(f"   (已移除@提及) 实际内容: {text}")

            # If no text content, skip
            if not text:
                logger.warning("消息内容为空，跳过处理")
                return

            # Forward to Dawei agent for processing
            logger.info("🚀 正在转发消息到Agent处理...")

            # Run async agent processing in event loop
            try:
                # Get or create event loop
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    # No running event loop, create new one
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                # Schedule the async processing
                if loop.is_running():
                    # If already in async context, create task
                    asyncio.create_task(self._process_message_async(
                        chat_id=chat_id,
                        text=text,
                        sender_open_id=sender_open_id,
                        message_id=message_id
                    ))
                else:
                    # Run in new event loop
                    loop.run_until_complete(self._process_message_async(
                        chat_id=chat_id,
                        text=text,
                        sender_open_id=sender_open_id,
                        message_id=message_id
                    ))

            except Exception as e:
                logger.error(f"❌ Agent处理失败: {e}", exc_info=True)
                # Fallback: send error message to Feishu
                try:
                    await self.send_message(
                        f"❌ 处理消息时出错: {str(e)[:100]}",
                        receive_id=chat_id
                    )
                except:
                    pass

        except Exception as e:
            logger.error(f"❌ Error processing message event: {e}", exc_info=True)

    async def _process_message_async(
        self,
        chat_id: str,
        text: str,
        sender_open_id: str,
        message_id: str
    ) -> None:
        """
        Process message with Agent asynchronously.

        This follows the same pattern as WebSocket chat handler:
        1. Create UserInputMessage
        2. Create Agent with Agent.create_with_default_engine()
        3. Call agent.process_message()
        """
        from pathlib import Path

        try:
            # Determine workspace path
            # Try to find workspace from plugin location
            plugin_dir = Path(__file__).parent.parent.parent
            workspace_path = plugin_dir

            logger.info(f"📂 使用工作区: {workspace_path}")

            # Import Agent
            try:
                from dawei.agentic.agent import Agent
                from dawei.workspace.user_workspace import UserWorkspace
                from dawei.entity.user_input_message import UserInputMessage
            except ImportError as e:
                logger.error(f"❌ 无法导入Agent模块: {e}")
                # Try with modified path for agent package
                import sys
                agent_path = Path("/home/dev007/ws/davybot/agent")
                if str(agent_path) not in sys.path:
                    sys.path.insert(0, str(agent_path))

                from dawei.agentic.agent import Agent
                from dawei.workspace.user_workspace import UserWorkspace
                from dawei.entity.user_input_message import UserInputMessage

            # Create UserInputMessage (same as WebSocket handler)
            user_input = UserInputMessage(text=text)
            logger.info(f"📝 创建UserInputMessage: {text[:50]}...")

            # Create and initialize Agent (same as _create_and_initialize_agent)
            logger.info("🤖 创建Agent实例...")
            agent = await Agent.create_with_default_engine(workspace_path)

            logger.info("⚙️ 初始化Agent...")
            await agent.initialize()

            logger.info("📨 调用agent.process_message()...")
            # Process the message - this will emit events via CORE_EVENT_BUS
            result = await agent.process_message(user_input)

            logger.info(f"✅ Agent处理完成: {result}")

            # Send acknowledgment to Feishu
            # Note: Agent events will be forwarded automatically via event handlers
            # For now, just send a simple acknowledgment
            # The actual response will come from the agent's event system

        except Exception as e:
            logger.error(f"❌ Agent处理消息失败: {e}", exc_info=True)
            # Send error message to Feishu
            try:
                await self.send_message(
                    f"❌ 处理失败: {str(e)[:100]}",
                    receive_id=chat_id
                )
            except Exception as send_err:
                logger.error(f"发送错误消息失败: {send_err}")
            # await self._forward_to_agent(chat_id, text, sender_open_id)

            # For now, send an auto-reply to confirm receipt
            # (Remove this if you only want agent to reply)
            # try:
            #     await self.send_message(
            #         f"✅ 收到消息: {text}",
            #         receive_id=chat_id
            #     )
            # except Exception as e:
            #     logger.error(f"Auto-reply failed: {e}")

        except Exception as e:
            logger.error(f"❌ Error processing message event: {e}", exc_info=True)

    # ========================================================================
    # Agent Forwarding (TODO: Implement based on agent system)
    # ========================================================================

    async def _forward_to_agent(
        self,
        chat_id: str,
        text: str,
        sender_open_id: str
    ) -> None:
        """
        Forward received message to Dawei agent for processing.

        TODO: Implement based on your agent system architecture.
        Common approaches:
        1. WebSocket connection to agent
        2. HTTP API call to agent endpoint
        3. Event bus / message queue
        """
        logger.info(f"📤 Forwarding message to agent: {text[:50]}...")

        # TODO: Implement one of the following:

        # Option 1: WebSocket (if agent has WS endpoint)
        # async with aiohttp.ClientSession() as session:
        #     await session.post("ws://localhost:8465/ws/agent/message", json={...})

        # Option 2: HTTP API
        # response = await self._client.post("/api/agent/message", json={...})

        # Option 3: Event emit (if using event bus)
        # await self._event_bus.emit("feishu_message", {...})

        pass


# ========================================================================
# Feishu Long Connection Handler
# ========================================================================

class FeishuLongConnectionHandler:
    """
    飞书长连接事件处理器 - WebSocket模式

    功能：
    1. 建立与飞书服务器的WebSocket长连接
    2. 接收飞书事件推送（消息、群聊变化等）
    3. 自动处理连接和重连
    4. 调用回调函数处理消息
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        encrypt_key: Optional[str] = None,
        message_callback: Optional[Callable] = None,
    ):
        """
        初始化长连接处理器

        Args:
            app_id: 飞书应用ID
            app_secret: 飞书应用密钥
            encrypt_key: 加密key（可选，如果飞书开放平台启用了加密）
            message_callback: 消息接收回调函数，签名为: callback(event_data: Dict[str, Any])
        """
        self.app_id = app_id
        self.app_secret = app_secret
        self.encrypt_key = encrypt_key
        self.message_callback = message_callback

        self.client: Optional[WsClient] = None
        self.is_running = False

        logger.info(f"FeishuLongConnectionHandler initialized: app_id={app_id}")

    def _handle_message_received(self, event) -> None:
        """处理接收到的消息事件"""
        try:
            logger.info("📩 ===== 收到飞书消息事件 =====")
            logger.info(f"📩 Event object type: {type(event)}")
            logger.info(f"📩 Event object: {event}")

            # 解析事件数据
            event_data = {
                "event_type": "im.message.receive_v1",
                "timestamp": datetime.now().isoformat(),
            }

            # 提取发送者信息
            if event.event and event.event.sender:
                sender = event.event.sender
                event_data["sender"] = {
                    "sender_id": sender.sender_id,
                    "sender_type": sender.sender_type,
                    "tenant_key": sender.tenant_key,
                }

            # 提取消息内容
            if event.event and event.event.message:
                message = event.event.message
                event_data["message"] = {
                    "message_id": message.message_id,
                    "chat_id": message.chat_id,
                    "chat_type": message.chat_type,
                    "message_type": message.message_type,
                    "content": message.content,
                    "create_time": message.create_time,
                    "update_time": message.update_time,
                }

            logger.info(f"📨 消息详情: {event_data}")

            # 调用回调函数处理消息
            if self.message_callback:
                try:
                    self.message_callback(event_data)
                    logger.info("✅ 消息已通过回调函数处理")
                except Exception as e:
                    logger.error(f"❌ 消息回调处理失败: {e}", exc_info=True)
            else:
                logger.warning("⚠️ 未设置消息回调函数，消息未处理")

        except Exception as e:
            logger.error(f"❌ 处理消息事件失败: {e}", exc_info=True)

    def start(self) -> None:
        """
        启动长连接（阻塞模式）

        这个方法会阻塞当前线程，建议在独立进程中运行
        """
        if self.is_running:
            logger.warning("FeishuLongConnectionHandler already running")
            return

        try:
            logger.info("🚀 启动飞书长连接...")

            # IMPORTANT: Must create new event loop BEFORE importing/using lark_oapi
            # This ensures the SDK's global loop variable uses our thread-local loop
            import asyncio

            # Clear any existing event loop for this thread
            try:
                existing_loop = asyncio.get_event_loop()
                if existing_loop.is_running():
                    # We're in a thread with a parent's loop - need to isolate
                    asyncio.set_event_loop(None)
            except RuntimeError:
                # No loop exists, which is fine
                pass

            # Create and set a new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            # Now import and create the client (it will use our new loop)
            from lark_oapi.ws.client import loop as sdk_loop

            # Patch the SDK's global loop to use our thread-local loop
            import lark_oapi.ws.client as sdk_client_module
            sdk_client_module.loop = loop

            # 创建事件处理器
            handler = (
                EventDispatcherHandler.builder(
                    self.encrypt_key or "",
                    "",  # verification_token，长连接模式不需要
                )
                .register_p2_im_message_receive_v1(self._handle_message_received)
                .register_p2_im_chat_member_bot_added_v1(self._handle_bot_added)
                .register_p2_im_chat_member_bot_deleted_v1(self._handle_bot_removed)
                # Register additional event handlers to avoid "processor not found" errors
                .register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(self._handle_p2p_chat_entered)
                .build()
            )

            # 创建WebSocket客户端
            self.client = WsClient(
                app_id=self.app_id,
                app_secret=self.app_secret,
                event_handler=handler,
                auto_reconnect=True,
            )

            self.is_running = True

            # 启动客户端（阻塞）
            logger.info("✅ 飞书长连接已启动，开始接收事件...")

            try:
                # Connect using the new loop
                loop.run_until_complete(self.client._connect())
                # Start ping loop
                loop.create_task(self.client._ping_loop())

                # Keep the loop running
                loop.run_forever()
            except Exception as e:
                logger.error(f"❌ WebSocket client error: {e}", exc_info=True)
                # Cleanup on error
                try:
                    loop.run_until_complete(self.client._disconnect())
                except:
                    pass
                raise
            finally:
                # Clean up the loop
                try:
                    loop.close()
                except:
                    pass

        except Exception as e:
            logger.error(f"❌ 启动飞书长连接失败: {e}", exc_info=True)
            self.is_running = False
            raise

    def stop(self) -> None:
        """停止长连接"""
        if not self.is_running:
            return

        logger.info("🛑 停止飞书长连接...")
        self.is_running = False

    def _handle_bot_added(self, event) -> None:
        """处理机器人被添加到群聊事件"""
        try:
            logger.info("🤖 机器人被添加到群聊")
            if event.event:
                logger.info(f"群聊信息: chat_id={event.event.chat_id}")

        except Exception as e:
            logger.error(f"❌ 处理机器人添加事件失败: {e}", exc_info=True)

    def _handle_bot_removed(self, event) -> None:
        """处理机器人被移出群聊事件"""
        try:
            logger.info("🚫 机器人被移出群聊")
            if event.event:
                logger.info(f"群聊信息: chat_id={event.event.chat_id}")

        except Exception as e:
            logger.error(f"❌ 处理机器人移出事件失败: {e}", exc_info=True)

    def _handle_p2p_chat_entered(self, event) -> None:
        """处理机器人进入P2P聊天事件"""
        try:
            logger.info("💬 机器人进入P2P聊天事件")
            if event.event:
                logger.info(f"事件详情: {event.event}")

        except Exception as e:
            logger.error(f"❌ 处理P2P聊天进入事件失败: {e}", exc_info=True)


# Export
__all__ = ["FeishuChannelPlugin", "RenderMode", "ReceiveIdType", "FeishuMessageResult", "FeishuLongConnectionHandler"]
