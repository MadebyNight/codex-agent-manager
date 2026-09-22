# Codex Agent Manager

本机 Web 面板，用于管理原生 Codex 与 Orca 托管 Codex 的子代理配置。

## 启动

Windows 安装 Python 3.11+ 后，双击 **启动面板.cmd**。首次启动在项目 `.venv` 安装唯一运行依赖 `tomlkit`，之后离线启动，无需 Agent 会话。

访问 http://127.0.0.1:8765 。关闭浏览器不会停止后台服务；双击 **停止面板.cmd** 可停止。重复启动会打开已有面板。

终端前台启动：`.venv\Scripts\python.exe -B -m app.server --open`。

## 范围与操作

- 原生：`%USERPROFILE%\.codex`，故意不读取可能被 Orca 覆盖的 `CODEX_HOME`。
- Orca：优先 `ORCA_CODEX_HOME`，否则 `%APPDATA%\orca\codex-runtime-home\home`。
- 同时修改两套：仅同步编辑过的字段，其余差异保留。目标一边缺失的自定义角色，以表单显示内容创建；预览列出全部文件。
- 官方角色 `default` / `worker` / `explorer`：仅允许 `gpt-` 开头的模型 ID，可调整推理强度，恢复默认移除覆盖，内置角色仍存在。GPT 限制是本工具产品规则，不宣称为 Codex 官方限制。
- 自定义角色：名称、描述、模型、推理强度、权限、提示词；不限模型类别，必须通过目标服务商的实际请求测试才能保存。已有名称不可重命名，避免更改调用方引用。
- 官方角色现有非 GPT 值只读展示、原样保留；编辑时要求改为 GPT，不会在启动时自动改写。
- 单文件原子替换；双套写入失败回滚。每次先备份，支持恢复最近一次操作。页面打开后文件被外部修改，保存会拒绝覆盖。
- 自定义 TOML 保留未知字段、注释和换行风格。已有 `[agents.<name>].config_file` 声明可读取与编辑；新增官方角色覆盖通过此机制保存模型与推理参数，不使用空提示词覆盖继承指令。

仅管理用户级 Codex 配置。不修改业务仓库、MCP、Skills、主模型或服务商配置；创建/移除官方覆盖时只修改主配置中的对应 `[agents.<name>]` 声明。暂不编辑目录外的 config_file 引用、共享链接或存在重复/损坏定义的配置。

## 连通性测试

点击“预览变更”→“测试连通性”，面板复用目标配置的服务商、API Key、环境变量、HTTP headers、query params，以及角色已有的服务商覆盖。请求 `POST /responses`，使用候选模型和推理强度，以 256 输出 Token 为预算请求简单文本。每套配置最长等待 45 秒，会产生少量服务商用量。不会启动 Codex 或子代理。

通过标准为成功返回可解析的 Responses 数据和非空文本，HTTP 200 本身不算通过。测试绑定候选模型、参数、地址和认证，10 分钟有效；全部目标通过后才能保存。

本版支持已有 API Key 认证的 Responses 兼容服务商；不管理服务商、不刷新 ChatGPT OAuth、不支持 Bedrock 专用认证。测试通过只证明文本生成可用，不能证明完整 Codex 工具调用兼容。高推理强度可能耗尽测试预算而不输出文本，面板会明确报告未通过。

密钥只在后端读取，不返回浏览器，不记录到服务日志。API 原始错误体不直接展示，避免上游代理回显凭据。

## 配置生效与 Orca

保存后新开 Codex 会话使用最新配置，当前会话不承诺热加载。Orca 可能随版本、账号或配置同步切换运行目录；面板明确显示目标路径，不修改 Orca 私有设置，不宣称已阻止它未来同步覆盖配置。若 Orca 切换账号，请对照其 `ORCA_CODEX_HOME` 重新启动面板。

本机核查时两套 agents 均为独立目录，8 个文件内容相同；skills 和 plugins 是共享 Junction，与此工具无关。

## 备份与恢复

本地 `.local/backups/*.json` 保存写入前后内容，不纳入 Git。备份可能包含 TOML 原有敏感配置，不要分享该目录。事务中断时下次启动检查 pending 记录；文件仍匹配事务前后状态才自动回滚，否则停止写入并提示人工检查。恢复会创建新的备份，因此可撤销恢复。

## 验证

`.venv\Scripts\python.exe -B -m unittest discover -s tests -v`

测试使用临时 Codex homes 和本地模拟 Responses 服务，不访问真实服务商、不改写真实配置。浏览器验收通过可选的 Playwright 开发依赖执行，详情见 `tests/browser_check.py`。

浏览器测试：安装 `requirements-dev.txt` 后，运行 `.venv\Scripts\python.exe -B -m tests.browser_check --browser "本机 Chromium 的完整路径"`；也可省略 `--browser` 使用已安装的 Playwright Chromium。验收截图：[桌面预览](docs/preview-desktop.png)、[窄屏预览](docs/preview-mobile.png)。

## 参考资料

- [Codex 子代理](https://developers.openai.com/codex/subagents)：内置角色、独立 agents TOML、配置继承。
- [Codex 配置参考](https://developers.openai.com/codex/config-reference)：agents config_file、模型服务商与认证字段。
- [Responses Create](https://developers.openai.com/api/reference/resources/responses/methods/create)：测试请求和输出结构。
- [Codex 官方角色源码](https://github.com/openai/codex/blob/main/codex-rs/core/src/agent/role.rs)。
- [TOML Kit](https://github.com/python-poetry/tomlkit)：保留格式的 TOML 编辑。

图标使用 Lucide 0.468.0，许可证见 `web/icons/LICENSE`。页面资源均本地提供，无 CDN 运行依赖。
