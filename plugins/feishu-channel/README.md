# 飞书渠道插件 (Feishu Channel Plugin)

飞书（Lark）消息集成插件，支持通过长连接或 HTTP 回调接收消息，并将 Agent 的响应发送回飞书。

## 版本

## 功能特性

### 消息接收
- ✅ **长连接模式** - WebSocket 长连接（推荐，无需公网 URL）
- ✅ **HTTP 回调模式** - HTTP POST 回调（需要公网 URL）
- ✅ **双模式自动切换** - 根据配置自动选择接收模式

## 安装

### 1. 复制插件到插件目录

```bash
cp -r feishu-channel ~/.dawei/plugins/
# 或
cp -r feishu-channel /path/to/workspace/.dawei/plugins/
```

### 2. 安装依赖

```bash
pip install lark-oapi>=1.5.3 aiohttp>=3.9.0
# 或使用项目中的依赖
```

### 3. 配置插件

创建配置文件 `~/.dawei/plugins/feishu-channel@0.2.0.json`:

```json
{
  "enabled": true,
  "activated": true,
  "settings": {
    "app_id": "cli_xxxxx",
    "app_secret": "xxxxx",
    "receive_id": "oc_xxxxx",
    "receive_id_type": "chat_id",
    "event_config": {
      "event_mode": "long_connection"
    },
    "enable_on_tool_call": false,
    "enable_on_task_complete": true,
    "enable_on_error": true,
    "render_mode": "auto",
    "max_retries": 3,
    "retry_delay": 1
  }
}
```

## 配置说明

### 必需配置

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `app_id` | 飞书应用 ID | `ss` |
| `app_secret` | 飞书应用密钥 | `xx` |
| `receive_id` | 接收消息的 ID | `xx` |

### 可选配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `receive_id_type` | 接收 ID 类型 | `chat_id` |
| `event_mode` | 事件接收模式 | `long_connection` |
| `enable_on_tool_call` | 工具调用通知 | `false` |
| `enable_on_task_complete` | 任务完成通知 | `true` |
| `enable_on_error` | 错误通知 | `true` |
| `render_mode` | 消息渲染模式 | `auto` |
| `max_retries` | 最大重试次数 | `3` |
| `retry_delay` | 重试延迟（秒） | `1.0` |

## 使用说明

### 1. 创建飞书应用

1. 访问[飞书开放平台](https://open.feishu.cn/)
2. 创建企业自建应用
3. 获取 `app_id` 和 `app_secret`
4. 配置权限：
   - `im:message` - 接收消息
   - `im:message:send_as_bot` - 发送消息
   - `im:chat` - 访问群聊

### 2. 配置事件订阅

#### 长连接模式（推荐）
- 无需配置公网 URL
- 适合内网环境
- 插件会自动建立 WebSocket 连接

#### HTTP 回调模式
- 需要公网 URL
- 配置请求 URL：`http://your-server:8466/feishu/events`
- 在飞书开放平台配置事件订阅

### 3. 获取 receive_id

#### 发送给群聊
- `receive_id`: 群聊 ID（如 `oc_xxxxx`）
- `receive_id_type`: `chat_id`

#### 发送给用户
- `receive_id`: 用户的 open_id（如 `ou_xxxxx`）
- `receive_id_type`: `open_id`

## 开发

### 目录结构
```
feishu-channel/
├── plugin.yaml              # 插件元数据
├── plugin.py                # 插件实现
├── config_schema.json        # 配置模式
├── plugin_base.py           # 基础类
└── README.md                # 本文档
```


## 更新日志

### v0.1.0
- 初始版本
- 基本消息收发功能
