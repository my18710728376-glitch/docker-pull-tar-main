# 更新日志

本文件记录本项目的重要变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本。

## [Unreleased]

### 修复

- 修复非 Docker Hub 仓库的单段仓库名被错误补全 `library/` 导致 404 的问题（如 `registry.k8s.io/pause`）。
- 修复断点续传在重试时沿用旧 `Range` 偏移、向分块文件重复追加数据的问题；服务端忽略 `Range` 返回 200 时改为从头覆盖写入。
- 修复 `arm/v7` 架构无法匹配清单的问题，改为同时校验 `architecture=arm` 与 `variant=v7`，并修正 `arm/v7` 产物路径中的非法斜杠。
- 为 manifest 请求补充超时，避免连接挂起导致下载任务无法结束。
- tar 先写入 `.tar.part` 再原子改名，避免中断时留下半成品镜像包。
- 修正默认仓库别名无效时在导入期抛异常导致服务无法启动的问题，改为告警并回退内置默认。
- 修正进度面板错误信息与目标镜像未做 HTML 转义的自注入风险。
- 修正 urllib3 版本告警的过滤正则。

### 变更

- 代理配置不再改写进程级 `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY`，仅作用于本工具的 HTTP 会话；「无代理」模式不再读取系统代理变量。
- 清理未使用的 `DownloadStats.start_time`，下载重试改为指数退避。

### 依赖

- 拆分运行时与开发依赖：新增 `requirements-dev.txt`，`pytest` 不再进入运行时依赖。
- 提升安全下限：`requests>=2.32.4`、`urllib3>=2.5.0`（修复 CVE-2024-47081、CVE-2025-50181）。

### 计划中

- 私有仓库认证（账号密码 / Token）
- 镜像层 SHA256 完整性校验
- 下载任务持久化与跨会话进度恢复
- 配置文件界面化管理与镜像站测速择优
- 扩展架构选项并支持按 manifest 动态生成
- HTTPS、访问鉴权与部署脚本
- 下载目录限定在允许的根路径内

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
