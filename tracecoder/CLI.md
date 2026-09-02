# TraceCoder 命令行使用手册

本文介绍 TraceCoder CLI 的配置、参数和常见使用方式。项目总览与快速开始见仓库根目录的 README。

## 运行方式

在仓库根目录直接运行：

```bash
python3 -m tracecoder.cli --help
```

如果已经安装为 Python 包，也可以使用：

```bash
tracecoder --help
```

## 基本语法

```text
tracecoder [任务] [选项]
```

任务可以作为位置参数传入：

```bash
python3 -m tracecoder.cli \
  --cwd /path/to/project \
  "创建一个命令行 Todo 项目，并写好依赖和使用说明"
```

也可以通过标准输入传入：

```bash
echo "为当前项目增加 JSON 导出功能" | \
  python3 -m tracecoder.cli --cwd /path/to/project
```

## 命令行参数

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `task` | 无 | 要交给 Agent 的自然语言任务 |
| `--cwd PATH` | 当前目录 | 目标项目目录，可以是 Git 仓库或普通文件夹 |
| `--config PATH` | `tracecoder.local.json` | 指定本地 JSON 配置文件 |
| `--model NAME` | 配置文件或 `deepseek-v4-pro` | 覆盖模型名称 |
| `--base-url URL` | 配置文件或 DeepSeek 地址 | 覆盖 OpenAI 兼容接口地址 |
| `--max-turns N` | `30` | 单次任务最大模型轮数，允许范围为 1～200 |
| `--context-chars N` | `80000` | 触发上下文压缩的字符阈值 |
| `--state-dir PATH` | `<TraceCoder>/.tracecoder` | 自定义运行记录和命令 HOME 的存放目录 |
| `-i, --interactive` | 关闭 | 启动交互式任务输入 |
| `--allow-network-commands` | 关闭 | 当任务表述较模糊时，显式允许网络 CLI |
| `--quiet` | 关闭 | 隐藏实时轨迹，只输出最终结果 |
| `-h, --help` | — | 显示帮助 |

## 本地配置

复制示例文件：

```bash
cp tracecoder.example.json tracecoder.local.json
```

配置格式：

```json
{
  "api_key": "",
  "base_url": "https://api.deepseek.com/v1",
  "model": "deepseek-v4-pro",
  "max_turns": 30,
  "context_chars": 80000
}
```

`tracecoder.local.json` 已被 Git 忽略。真实 API Key 不得写入示例文件、README 或提交记录。

配置优先级从高到低为：

1. 命令行参数；
2. `tracecoder.local.json`；
3. `DEEPSEEK_API_KEY`、`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`TRACECODER_MODEL` 等环境变量；
4. 程序默认值。

## 单次任务

### 从零创建项目

```bash
mkdir -p /path/to/new-project
python3 -m tracecoder.cli \
  --cwd /path/to/new-project \
  "创建一个记账 CLI，写好源码、requirements.txt 和中文版 README"
```

### 修改已有项目

```bash
python3 -m tracecoder.cli \
  --cwd /path/to/existing-project \
  "增加 CSV 导出功能并更新使用文档"
```

## 交互式 CLI

交互模式适合在同一个目标项目中连续提交多个编码任务，不需要每次重新输入启动命令：

```bash
python3 -m tracecoder.cli --interactive --cwd /path/to/project
```

如果后续任务可能明确涉及下载或其他网络 CLI，可以在启动时增加：

```bash
python3 -m tracecoder.cli \
  --interactive \
  --allow-network-commands \
  --cwd /path/to/project
```

启动后会显示当前工作目录并进入 `tracecoder>` 输入提示符：

```text
TraceCoder interactive · workspace: /path/to/project
输入任务并回车；/help 查看命令，/quit 退出。

tracecoder>
```

在提示符后直接输入自然语言任务并回车。每个任务执行期间，终端会实时显示模型轮次、工具调用、文件修改以及用户明确要求的命令输出。

示例会话：

```text
tracecoder> 创建一个命令行 Todo 项目，并补充使用文档
... 实时执行轨迹 ...
结束: verified_complete

tracecoder> 再增加 JSON 导出功能
... 实时执行轨迹 ...
结束: verified_complete
```

每条普通输入都会启动一个新的 Agent 任务，但所有任务使用同一个 `--cwd` 目标目录，因此后续任务可以继续修改前一任务生成的项目文件。

### 交互命令

| 命令 | 作用 |
|---|---|
| `/help` | 显示交互命令帮助 |
| `/quit` | 结束交互模式 |
| `/exit` | 结束交互模式 |

空输入会被忽略。按 `Ctrl+C` 或发送 EOF 也可以退出。单个任务失败时，交互会话不会自动关闭，可以继续输入新的修复任务。

## 命令执行原则

TraceCoder 始终以完成项目代码为主。普通编码任务默认只读取、搜索和修改文件，不会为了满足 Agent 流程主动创建测试、安装依赖或搭建验证环境。

当用户明确要求安装、运行、测试、验证、下载或其他命令工作时，Agent 可以调用 `run_command`。例如：

```bash
python3 -m tracecoder.cli \
  --cwd /path/to/project \
  "完成代码，然后安装依赖并启动一次，报告实际输出"
```

```bash
python3 -m tracecoder.cli \
  --cwd /path/to/project \
  "修复错误并运行项目已有的测试，不要新建测试模块"
```

任务中包含“下载”“安装”“联网”“git clone”等明确意图时，会自动允许相应网络操作。表述较模糊时可增加：

```bash
--allow-network-commands
```

命令执行不是所有任务的强制完成条件，测试结果也不会自动成为普通代码任务的统一验收门槛。

## 实时轨迹

默认终端会实时显示模型轮次、工具调用、文件修改、命令输出、上下文压缩和交付检查结果。使用 `--quiet` 可以关闭过程输出。

完整记录默认保存在：

```text
<TraceCoder>/.tracecoder/runs/<时间戳>/
```

其中包含 `events.jsonl`、`summary.json`、`final.diff` 和 `transcript.md`。这些记录不会写入用户目标项目。

## 常见结束状态

| 状态 | 说明 |
|---|---|
| `verified_complete` | 模型主动结束且文件级交付检查通过 |
| `verified_complete_auto` | 达到轮数上限后自动检查通过 |
| `max_turns` | 达到轮数上限且交付检查未通过 |
| `stagnation` | 连续重复相同行为 |
| `model_errors` | 模型接口连续返回错误 |
| `empty_actions` | 模型没有调用工具 |
| `interrupted` | 用户中断 |
| `internal_error` | TraceCoder 内部异常 |

交付检查只确认存在有效文件修改、补丁格式正确且修改规模不过大，不会强制运行用户项目或测试。
