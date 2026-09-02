# TraceCoder

Git 仓库：https://github.com/Gaskoda/coding-agile

TraceCoder 是一个独立设计和实现的轻量编程智能体。它通过大语言模型原生 Tool Calling 接口读取、搜索和修改本地项目文件，并根据用户任务完成代码编写。项目未使用 LangChain、LlamaIndex、OpenAI Agents SDK、Claude Agent SDK、AutoGen、CrewAI 等 Agent 框架，也不依赖服务端托管的代码执行或文件工具。

## 特色功能

1. 支持从零创建项目，也支持修改 Git 仓库和普通目录。
2. 默认优先交付源码、依赖清单和使用文档，不为满足 Agent 流程强制创建测试或搭建验证环境。
3. 用户明确要求时，可执行安装依赖、创建环境、运行项目、测试程序或下载资源等命令。
4. 终端实时显示模型轮次、工具调用、文件修改、命令输出和交付结果。
5. 支持分层 `AGENTS.md`、上下文压缩、重复行为检测和任务状态保留。
6. 运行记录保存在 TraceCoder 自身的 `.tracecoder` 目录，不污染用户项目。

详细命令行参数和交互式 CLI 用法见 [`tracecoder/CLI.md`](tracecoder/CLI.md)。

## 配置与运行

要求 Python 3.10 及以上版本。TraceCoder 运行时只使用 Python 标准库；处理普通非 Git 目录时需要 GNU patch。

```bash
cp tracecoder.example.json tracecoder.local.json
```

在 `tracecoder.local.json` 中填写自己的 API Key、接口地址和模型名称。该文件已被 Git 忽略，真实密钥不得提交到仓库。

单次运行：

```bash
python3 -m tracecoder.cli \
  --cwd /path/to/project \
  "创建一个命令行 Todo 项目，并写好依赖和使用说明"
```

交互运行：

```bash
python3 -m tracecoder.cli -i --cwd /path/to/project
```

任务明确需要联网时，可添加 `--allow-network-commands`。

## 架构与安全

`agent.py` 实现模型—工具循环、任务状态和结束条件；`tools.py` 实现文件浏览、搜索、读取、补丁修改及受限命令执行；`context.py` 和 `task_state.py` 管理长上下文；`instructions.py` 加载分层规则；`safety.py` 负责工作区隔离、敏感文件保护和危险命令拦截；`workspace.py` 负责普通目录快照和差异生成。

所有模型文件操作都限制在目标目录内。系统禁止读取常见凭据、逃出工作区、危险删除、推送代码、发布或部署。API Key 只从环境变量或本地配置文件读取，不写入仓库和运行记录。交付检查只确认存在有效文件修改，不强制执行测试。
