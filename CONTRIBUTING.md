# 贡献指南

感谢参与本项目。本文说明开发环境、编码约定与提交流程，提交前请先阅读 [README](./README.md) 了解项目定位与功能。

## 1. 项目定位与约束

- 本项目是面向本地 B/S 部署的镜像拉取工具，唯一运行入口为 `python app.py`。
- 不接受重新引入命令行客户端、EXE 打包或桌面客户端形态的变更。
- 不引入与本项目无关的框架、构建链或前端构建步骤；新增依赖需在 PR 中说明必要性。
- 保持对公开镜像仓库的匿名访问能力，不加入需要账号体系的强制登录逻辑。

## 2. 开发环境

```bash
git clone <仓库地址>
cd docker-pull-tar-main

python -m venv .venv
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

启动服务：

```bash
python app.py
```

浏览器访问 `http://127.0.0.1:7860`。

## 3. 目录职责

| 路径 | 职责 |
| --- | --- |
| `app.py` | 页面构建、事件绑定、下载执行器、搜索与文件管理 |
| `registry_config.py` | 仓库配置解析、镜像地址重写、连通性预检查 |
| `official_images.py` | 内置官方镜像清单与本地检索 |
| `tests/` | 单元测试与集成测试 |
| `screenshots/` | README 使用的界面截图 |
| `downloads/` | 运行时产物目录，不提交镜像文件 |

## 4. 编码规范

- 遵循 PEP 8，缩进使用 4 个空格，行宽建议不超过 100 字符。
- 命名：模块与函数使用 `snake_case`，类使用 `PascalCase`，常量使用 `UPPER_SNAKE_CASE`。
- 公共函数补充文档字符串，说明输入、输出与异常；复杂分支添加必要注释。
- 网络请求必须设置超时；新增外部请求需考虑失败降级，避免页面整体不可用。
- 用户可见文案统一使用中文，日志与异常信息避免泄露本机绝对路径以外的敏感信息。
- 不提交调试残留（`print` 调试输出、临时文件、注释掉的代码块）。

## 5. 提交规范

采用约定式提交（Conventional Commits），提交信息使用中文描述，格式：

```text
<type>: <简要说明>
```

常用 type：

| type | 用途 |
| --- | --- |
| `feat` | 新增功能 |
| `fix` | 修复缺陷 |
| `docs` | 文档变更 |
| `test` | 测试相关 |
| `refactor` | 不改变行为的重构 |
| `chore` | 构建、依赖、杂项 |

示例：

```text
feat: 支持配置文件扩展自定义镜像仓库
fix: 修正 OCI 清单请求缺少媒体类型导致的 404
docs: 补充 B/S 部署步骤与界面截图
```

要求：

- 一个提交只做一件事，避免功能与格式化混在同一提交。
- 不提交 `downloads/`、`*.tar`、虚拟环境、缓存目录与本地 `registry_config.json`。
- 提交前确认 `git status` 中无意外文件。

## 6. 分支与流程

1. 从主分支创建分支：`feature/<简述>`、`fix/<简述>`、`docs/<简述>`。
2. 完成开发与自测后提交 PR，说明变更动机、影响范围与验证方式。
3. PR 描述中附上关键界面变更截图（如涉及 UI）。
4. 关联相关 Issue；评审意见处理完毕后再合并。

## 7. 测试要求

```bash
# 单元测试（默认不访问外网）
python -m pytest

# 真实仓库连通性与拉取测试（需显式开启）
# Windows PowerShell
$env:RUN_REGISTRY_INTEGRATION_TESTS = "1"
python -m pytest tests/test_integration_registries.py

$env:RUN_REGISTRY_PULL_TESTS = "1"
python -m pytest tests/test_integration_registries.py -k real_pull
```

- 新增或修改功能必须补充测试，覆盖正常流程与边界场景。
- 涉及网络请求的测试需使用 `tests/fakes.py` 中的伪造会话，避免依赖真实网络。
- 涉及界面结构的改动，请在本地启动服务并实际操作验证，同时更新 `screenshots/` 中受影响的截图。

## 8. PR 检查清单

- [ ] `python -m pytest` 全部通过
- [ ] 新增功能有对应测试
- [ ] 文档（README / CHANGELOG）已同步更新
- [ ] 未提交运行时产物与本地配置
- [ ] 界面改动已本地验证，必要时更新截图
- [ ] 不包含与本次变更无关的格式化或重构

## 9. 问题反馈

提交 Issue 时请附上：

- 操作系统与 Python 版本、Gradio 版本
- 目标镜像完整名称与 Tag
- 页面错误提示或服务端堆栈
- 已尝试的排查步骤（切换仓库、代理、SSL 校验等）
