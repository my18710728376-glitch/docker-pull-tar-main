# 更新日志

本文件记录本项目的重要变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本。

## [Unreleased]

### 计划中

- 私有仓库认证（账号密码 / Token）
- 镜像层 SHA256 完整性校验
- 下载任务持久化与跨会话进度恢复
- 配置文件界面化管理与镜像站测速择优

## [2.1.0] - 2026-11-10

### 变更

- 移除命令行客户端、1ms 专版客户端及相关测试，项目仅保留 Web 服务入口。
- 移除 PyInstaller 打包脚本、EXE 版本资源与打包说明，不再分发可执行文件。
- 移除客户端专用依赖 `tqdm`，依赖清单仅保留 Web 运行与测试所需项。

### 新增

- 新增 `screenshots/` 目录，补充页面首屏、全局设置、搜索结果、参数选择、下载进度、下载完成与文件管理截图。
- 新增 `.env.example`、`registry_config.example.json` 作为环境变量与仓库配置模板。
- 新增 `CONTRIBUTING.md`、`CHANGELOG.md`、`pyproject.toml`、`.editorconfig`，完善开发与协作规范。
- 服务启动时加载 `favicon.ico` 作为浏览器页签图标。

### 修复

- 修复进度面板右侧「百分比 | 已下载/总大小」在长文本下被裁切的问题，改为按内容宽度自适应。
- 修正 `registry_config.py` 模块说明中残留的命令行工具描述。

### 文档

- 重写 README 为 B/S 部署结构，补充项目来源、与原项目差异、部署步骤、功能使用说明与常见问题排查。
- 补充启动成功判定标准、界面元素说明与产物命名规则。

## [上游基线]

本项目来源于 GitHub 开源项目 [topcss/docker-pull-tar](https://github.com/topcss/docker-pull-tar)，在其基础上完成二次优化。上游历史版本与变更请参阅原项目仓库。
