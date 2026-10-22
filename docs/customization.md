# 界面与自定义

## 前端组成

以下路径均以仓库根目录为准。页面结构在 index.html。FastAPI 的 /static 服务提供 CSS 和浏览器原生 JavaScript 模块。构建和运行不需要 npm。

| 文件 | 适合修改什么 |
| --- | --- |
| static/styles.css | 字体、字号、配色、侧栏宽度、消息宽度、圆角、手机布局 |
| static/app.js | 消息发送、上传、项目搜索/切换、进度、偏好设置和控件事件 |
| static/markdown.js | 模型消息中的标题、列表、表格、代码和链接展示 |
| index.html | 页面布局、组件位置、图标及初始控件 |

界面采用左侧项目/模型、右侧主对话。设置和日志按需展开。消息区限制阅读宽度，输入框固定在底部；手机端通过遮罩打开侧栏。

## 字体、颜色与布局

样式入口为 :root：

~~~css
:root {
  --font-ui: "Noto Sans SC", "Segoe UI", "PingFang SC", "Microsoft YaHei UI", sans-serif;
  --font-code: "Cascadia Code", Consolas, monospace;
  --message-size: 16px;
  --sidebar-width: 286px;
  --conversation-width: 850px;
  --accent: #23755c;
}
~~~

字体使用本机已安装字体，不请求第三方字体服务器。未安装 Noto Sans SC 时自动回退。若后续自托管其他字体，可在 static/fonts 放置允许分发的字体，通过 @font-face 引入。

data-theme 控制浅色/深色变量；data-font 和 data-text-size 控制字体和字号。浏览器只在 localStorage 保存这些显示偏好，不保存密钥和图片内容。

更换品牌名只需修改 index.html 的品牌与标题，以及 app.js 中助手消息的名称。

## 消息与交互

~~~text
输入文字 / 选择图片
  → POST /api/chat
  → 获取 job_id
  → 轮询 GET /api/jobs/{job_id}
  → 根据真实事件显示规划 / CAD / 保存进度
  → 展示最终答复
  → GET /api/status 刷新参数、项目与下载文件
~~~

Enter 发送，Shift + Enter 换行。判断键盘输入法组合状态，避免中文选词时误发送。上传、粘贴、拖放进入同一图片队列，发送失败时保留草稿和附件。

模型答复通过 DOM 节点渲染 Markdown 子集。普通文本、代码和表格单元格都作为文本写入；不插入模型生成的原始 HTML。代码块及完整答复有独立复制操作。

当前使用真实任务事件轮询，没有逐 token 流式答复。实现 SSE 时可以沿用现有消息组件，在后端增加流事件，保持最终文件和任务状态仍由 CadAgent 确认。

## 主要 API

| 接口 | 用途 |
| --- | --- |
| GET /api/status | 模型连接、当前项目、设计参数、项目元数据和文件 |
| POST /api/chat | 文字及 Base64 图片任务 |
| POST /api/local | 有限配方的本地规则验证 |
| POST /api/new /api/open | 新建或打开项目 |
| POST /api/continue /api/stop | 继续或停止后续调用 |
| POST /api/config | 连接配置 |
| POST /api/inspect | 检查并保存模型 |
| GET /api/jobs/{id} | 任务事件与结果 |
| GET /files/{name} | 当前项目的可下载文件 |

写请求须携带页面会话标记 X-Agent-Token。更换前端时从首页注入的 meta[name="agent-token"] 读取，保持与同一后端会话一致。

## 扩展建模能力

1. 在 recipes.py 新增参数边界明确的构建函数，返回命令列表与规格。
2. 在 CUSTOM_TOOLS 和 BUILDERS 注册 schema 与实现。
3. 在 agent.py 的 system_prompt 说明适用场景、默认值和必须补齐的参数。
4. 对真实 CAD 结果增加独立尺寸、实体和文件检查。

附加设计规范优先使用 agent.instructions_file；无需修改系统提示词就能设置单位、默认结构和交付要求。

新增 CAD 工具应继续经过 validate_policy，明确输入和输出位置；多个写操作保持串行。新增后端可以参考现有工具接口设计适配层。

## Python 嵌入

当前采用同目录 Python 模块，尚未发布为 pip 包：

~~~python
import asyncio
from agent import Settings
from cli import run_task

async def main():
    settings = Settings()
    settings.visible = False
    settings.connect = ""
    result = await run_task(settings, "画一个80×60×30的实心长方体")
    print(result["status"], result["directory"])

asyncio.run(main())
~~~

模型模式需要已配置凭证。终端退出码：0 完成、1 失败、2 等待继续、3 等待补参、130 中断。

## 自定义运行环境

网页入口读取 SOLIDCRAFT_CLI、SOLIDCRAFT_PARTS、SOLIDCRAFT_VISIBLE 和 SOLIDCRAFT_CONNECT 等环境变量；终端入口可用 --config agent.local.json 管理多套模型与工作流。JSON 中的路径相对配置文件解析，不会因终端工作目录变化而改变。

DEEPSEEK_API_KEY 与 CUSTOM_MODEL_API_KEY 的示例值统一为 `Xx`。本项目不自动读取 .env；请在启动进程前设置环境变量，或在网页中配置 DeepSeek 连接。不要把 .private、参考照片、生成模型和个人配置提交到仓库。

修改后先运行 README 中的离线测试，再用自己的 CAD 环境核对实际几何；本仓库不包含运行时二进制和历史测试模型。
