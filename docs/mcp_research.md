# MCP协议调研报告

> 调研时间：2026-07-16
> 调研人：第4组
> 目的：为ToolRegistry和SkillLibrary的接口设计提供理论参考

---

## 一、MCP协议概述

**MCP（Model Context Protocol）** 是由 Anthropic 推出的开放协议，旨在为 AI 模型（LLM）提供标准化的外部工具和数据源接入方式。类比于 USB-C 为硬件设备提供的统一连接标准，MCP 为 AI 应用提供了统一的接口标准。

**核心思想**：将 AI 应用的能力从"对话"扩展到"行动"——让 LLM 能够通过标准接口调用外部工具、读取外部数据源。

### 1.1 协议定位

| 层级 | 说明 |
|------|------|
| 传输层 | JSON-RPC 2.0 协议，支持 stdio 和 Streamable HTTP |
| 能力层 | Tools（工具）、Resources（资源）、Prompts（提示词模板） |
| 控制层 | Sampling（采样）、Roots（根目录） |

### 1.2 与本项目的关联

本项目（Agentic OS 第4组）负责的 **ToolRegistry（工具注册表）** 和 **SkillLibrary（技能库）**，与 MCP 中的 **Tools** 概念高度对应。MCP 的 Tool 设计规范可直接指导我们组接口的设计。

---

## 二、MCP核心架构

### 2.1 整体架构

```
Host（宿主机，如Claude Desktop）
    │
    ├── Client（MCP客户端）
    │       │
    │       ├── Server A（MCP服务器，提供Tools）
    │       ├── Server B（MCP服务器，提供Resources）
    │       └── Server C（MCP服务器，提供Prompts）
    │
    └── Transport（stdio / Streamable HTTP）
```

MCP 采用**客户端-服务器架构**：
- **Host**：LLM 应用程序（如 Claude Desktop、IDE 插件）
- **Client**：与 Server 建立一对一连接
- **Server**：提供工具、资源、提示词等能力

### 2.2 核心通信模式（JSON-RPC 2.0）

所有通信采用 JSON-RPC 2.0 协议，方法调用格式：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/list",
  "params": {}
}
```

成功响应：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": { ... }
}
```

错误响应：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32601,
    "message": "Method not found"
  }
}
```

---

## 三、MCP Tools 规范详解

Tool 是 MCP 中最重要的能力原语，允许 LLM **通过 Server 执行操作**（如读写文件、执行命令、调用 API）。

### 3.1 Tool 定义字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 工具唯一标识，1-128字符，仅允许 ASCII 字母/数字/下划线/连字符/点 |
| `title` | string | 否 | 人类可读的显示名称 |
| `description` | string | 否 | 工具功能介绍，LLM 据此判断何时调用 |
| `inputSchema` | object | 是 | JSON Schema（2020-12）定义参数结构 |
| `outputSchema` | object | 否 | 定义结构化输出格式 |
| `annotations` | object | 否 | 元数据：audience（受众）、priority（优先级）等 |
| `icons` | array | 否 | UI 图标（2025-11-25 新增） |

### 3.2 Tool 核心方法

#### `tools/list` — 发现可用工具

**请求**：无参数（支持分页：`cursor` 参数）

**响应**示例：

```json
{
  "tools": [
    {
      "name": "copy_file",
      "description": "复制文件到目标路径",
      "inputSchema": {
        "type": "object",
        "properties": {
          "src": {
            "type": "string",
            "description": "源文件路径"
          },
          "dest": {
            "type": "string",
            "description": "目标文件路径"
          }
        },
        "required": ["src", "dest"]
      }
    }
  ]
}
```

#### `tools/call` — 调用指定工具

**请求**：

```json
{
  "method": "tools/call",
  "params": {
    "name": "copy_file",
    "arguments": {
      "src": "/path/to/source.txt",
      "dest": "/path/to/dest.txt"
    }
  }
}
```

**成功响应**：

```json
{
  "content": [
    {
      "type": "text",
      "text": "已复制：/path/to/source.txt -> /path/to/dest.txt"
    }
  ],
  "isError": false
}
```

**失败响应**：

```json
{
  "content": [
    {
      "type": "text",
      "text": "错误：文件不存在"
    }
  ],
  "isError": true
}
```

> **重要**：错误应通过 `isError: true` 标记而非用 JSON-RPC 错误码，这样 LLM 可以读取错误信息并尝试自行修正。

#### `notifications/tools/list_changed` — 工具列表变更通知

当 Server 的工具列表发生变化时，若声明了 `listChanged` 能力，则发送此通知。

### 3.3 结果类型

Tool 可以返回以下类型的内容：

| 类型 | 说明 |
|------|------|
| `text` | 文本内容 |
| `image` | 图片（base64 + MIME类型） |
| `audio` | 音频（base64 + MIME类型） |
| `resource` | 资源引用 |
| `embedded` | 嵌入式资源 |

---

## 四、MCP 其他核心概念

### 4.1 Resources（资源）

- **用途**：暴露外部数据（文件、日志、数据库记录等）
- **控制权**：应用程序控制（Host/Client 决定是否使用）
- **读写**：默认可读，可选支持写入
- **关键方法**：`resources/list`、`resources/read`、`resources/subscribe`

### 4.2 Prompts（提示词模板）

- **用途**：提供可复用的 LLM 交互模板
- **控制权**：用户控制（用户主动选择，类似斜杠命令）
- **关键方法**：`prompts/list`、`prompts/get`
- **特点**：支持参数、动态上下文注入、多步骤工作流

### 4.3 Sampling（采样）

- **用途**：Server 请求 Client 代为请求 LLM 生成内容
- **控制权**：由 Server 发起，但需要用户审批
- **关键方法**：`sampling/createMessage`
- **安全性**：用户必须显式批准每次采样请求

---

## 五、MCP 传输层

| 传输方式 | 说明 | 适用场景 |
|----------|------|----------|
| **stdio** | Client 将 Server 作为子进程启动，通过 stdin/stdout 通信 | 本地开发、单机部署 |
| **Streamable HTTP** | 基于 HTTP 的双向流式通信 | 网络部署、生产环境 |
| HTTP+SSE | 旧版 HTTP 方案（已废弃） | 不推荐使用 |

---

## 六、MCP 安全模型

1. **人类参与**：工具调用必须提供确认提示
2. **访问控制**：Server 必须验证输入、实施速率限制
3. **透明性**：向用户展示工具调用的输入参数
4. **输出清理**：Server 清理输出，Client 传递 LLM 前验证
5. **工具即代码执行**：Tool 本质是任意代码执行，必须谨慎处理

---

## 七、MCP 对第4组接口设计的指导

### 7.1 可参考的设计要点

| MCP设计 | 本组的对应实现 | 改进建议 |
|---------|---------------|---------|
| `name` + `description` + `inputSchema` 的Tool定义 | 目前只有 `name` -> `func` 的简单映射 | 增加工具的 `description` 和 `inputSchema` 属性，使工具自描述 |
| `tools/list` 返回完整工具列表 | `list_tools()` 只返回工具名列表 | 补充返回工具的完整元数据（名称、描述、参数Schema） |
| `tools/call` 统一调用入口 | `call(name, params)` 已有 | 基本符合，可增加参数校验逻辑 |
| JSON-RPC 2.0 统一格式 | 自定义 `{success, result, error}` 格式 | 可考虑引入类似 `isError` 的语义标记 |
| Tool Execution Error（非Protocol Error） | 目前异常都返回统一格式 | 区分"参数错误"和"执行错误" |
| 分页支持 | 未实现 | 当工具数量多时可考虑 |

### 7.2 建议的 Tool 元数据增强

参考 MCP 的 Tool 定义，建议我们组的工具注册信息增强为：

```python
{
    "name": "copy_file",
    "title": "复制文件",
    "description": "将文件从源路径复制到目标路径，保留元数据",
    "inputSchema": {
        "type": "object",
        "properties": {
            "src": {"type": "string", "description": "源文件路径"},
            "dest": {"type": "string", "description": "目标文件路径"}
        },
        "required": ["src", "dest"]
    }
}
```

### 7.3 与第3组（AppAgent）的对接设计

参考 MCP 的 Client-Server 模型，第3组（AppAgent）充当 MCP 中的 Host/Client 角色，第4组充当 Server 角色：

```
第3组 AppAgent (Host/Client)
    │
    ├── tools/list ──────────→ 第4组 OSServiceAPI (Server)
    │                           - 返回可用工具列表
    │
    └── tools/call ───────────→ 第4组 OSServiceAPI (Server)
        "copy_file"             - 执行具体工具
        {src, dest}             - 返回执行结果
```

---

## 八、Python MCP SDK 参考

### 8.1 官方SDK

- **官方 Python SDK**：`mcp` 包（PyPI: mcp）
- **FastMCP**：高层抽象，通过装饰器自动生成 Schema
- **Core Server**：底层控制，手动处理 `list_tools` / `call_tool`

### 8.2 FastMCP 示例（供参考）

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("OS Service")

@mcp.tool()
def copy_file(src: str, dest: str) -> str:
    """复制文件到目标路径"""
    import shutil
    shutil.copy2(src, dest)
    return f"已复制：{src} -> {dest}"

mcp.run(transport="stdio")
```

---

## 九、参考资料

1. MCP 官方规范文档 — https://modelcontextprotocol.io/specification/2025-11-25/
2. MCP Tools 规范 — https://modelcontextprotocol.io/specification/2025-11-25/server/tools.md
3. MCP 架构说明 — https://modelcontextprotocol.io/specification/draft/architecture
4. Python MCP SDK (PyPI) — https://pypi.org/project/mcp/
5. MCP 入门指南 — https://dev.to/gpuneet/the-model-context-protocol-in-python-34ni
6. FastMCP 示例 — https://github.com/PrefectHQ/fastmcp
7. UFO² 论文 — 微软研究院，Windows Agent 相关论文
