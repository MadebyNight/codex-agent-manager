# 项目约定

- 本项目是本地 Web 面板，只管理所选用户级原生 Codex 和 Orca 托管 Codex 配置目录中的子代理。功能边界和已确认行为见 `README.md` 与 `docs/design.md`。
- 后端在 `app/`，前端在 `web/`，测试在 `tests/`。`app/config.py` 读取配置并计算角色有效值；`app/manager.py` 提供状态、预览、连通性测试、写入与恢复；`app/settings.py` 处理目录检测和记忆。
- 默认目录由 `app/config.py:default_homes` 检测；用户选择保存在 `.local/settings.json`。模型建议从所选配置的服务商 `GET /models` 接口获取，当前角色有效模型仅作回退；服务商不提供列表时仍可手动输入模型 ID。
- 修改角色时保留 TOML 中无关字段、注释和原有内容；保存流程需要预览、实际模型连通性测试及用户确认。不得用测试写入真实用户的 Codex 配置。
- 运行相关单元测试：`.venv\Scripts\python.exe -B -m unittest discover -s tests -v`。端到端浏览器验收见 `tests/browser_check.py`；测试使用临时配置目录和本地模拟服务。
- 发布包仅面向 Windows x64；依赖和构建步骤见 `requirements*.txt`、`scripts/build_release.py` 及 `README.md`。`.venv/`、`.local/`、`dist/`、`build/` 不纳入 Git。
