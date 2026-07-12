# LoreLoop

[English](README.md) | [简体中文](README.zh-CN.md)

让编码代理真正复用项目知识。

LoreLoop 从现有代码、运行中的 Web 应用和已经验收的开发结果中，整理出一份本地、可审核
的项目知识库。你继续使用 Codex、Claude Code、OpenCode 或 co-mind；LoreLoop 在背后
提供相关知识，并保留验收证据。

> 目前是 early alpha。完整闭环已经可用，但接口仍可能调整。

## 为什么需要它

新代理进入老项目时，几乎总是从零开始。会话记忆还没有内容，文档可能已经过期，而代理
自己写下的笔记也不应该自动变成项目事实。

LoreLoop 补上三步：

- **反构**：从代码和真实页面中提取断言级知识，并保留源码位置、提交和页面快照。
- **使用**：为当前需求找出少量相关知识，明确区分“已确认约束”和“仍需核对的参考”。
- **回流**：记录验收证据，只把已经接受的结果带回知识库。

它不是新的聊天入口，也不会替代你正在使用的编码代理。

## 安装

### 让当前编码代理安装

把下面这段直接发给 Codex、Claude Code、OpenCode 或 co-mind：

```text
请为正在运行本次对话的编码代理安装并配置 LoreLoop。

请完整阅读这个 README 的“安装”部分并严格执行，不要只做总结：
https://github.com/zhangguiping-xydt/loreloop/blob/main/README.zh-CN.md

请识别当前宿主，使用对应选项安装 LoreLoop，然后运行 loreloop doctor 和当前宿主的
status 命令。

不要要求我单独安装或理解其他执行组件；不要直接修改 .loreloop、宿主配置或
marketplace 文件；安装过程中不要执行 trust reset、complete、harvest 或知识策展。
```

### 从 GitHub Release 安装

先下载安装器，不要把远端脚本直接通过管道交给 shell。

Linux/macOS：

```bash
curl -fLO https://github.com/zhangguiping-xydt/loreloop/releases/latest/download/install-loreloop.sh

# 只选择当前宿主：
sh install-loreloop.sh --codex
sh install-loreloop.sh --claude
sh install-loreloop.sh --opencode
sh install-loreloop.sh --comind
```

Windows PowerShell：

```powershell
Invoke-WebRequest https://github.com/zhangguiping-xydt/loreloop/releases/latest/download/install-loreloop.ps1 -OutFile install-loreloop.ps1

# 只选择当前宿主：
.\install-loreloop.ps1 -Codex
.\install-loreloop.ps1 -Claude
.\install-loreloop.ps1 -OpenCode
.\install-loreloop.ps1 -CoMind
```

只有需要浏览器探索和浏览器验收时，才增加 `--with-web` 或 `-WithWeb`。

### 首个 Release 发布前

仓库还没有 GitHub Release 时，可以直接安装当前分支：

```bash
uv tool install --force \
  'loreloop[web] @ git+https://github.com/zhangguiping-xydt/loreloop.git@main'
```

然后只连接当前宿主：

```bash
loreloop codex install --source zhangguiping-xydt/loreloop --ref main
loreloop claude install --source zhangguiping-xydt/loreloop
loreloop opencode install
loreloop comind install --source zhangguiping-xydt/loreloop
```

不需要浏览器能力时移除 `[web]`。正式 Release 已存在但校验失败时，不能改用可变分支
绕过校验。

### 安装后的入口

| 宿主 | 使用方式 |
|---|---|
| Codex | 新开线程后调用 `$loreloop`，或直接用自然语言要求使用 LoreLoop |
| Claude Code | 新开会话后直接要求使用 LoreLoop |
| OpenCode | 新开会话后运行 `/loreloop <需求>` |
| co-mind | 新开会话后直接要求使用 LoreLoop |

检查安装：

```bash
loreloop doctor
loreloop codex status      # 或 claude / opencode / comind
```

## 第一个项目

在项目中初始化：

```bash
cd your-project
loreloop init --skill
```

从代码建立第一版知识：

```bash
loreloop ingest --from code .
loreloop knowledge review
```

然后留在当前编码代理会话里，直接说：

```text
使用 LoreLoop 给上传接口增加限流。
```

宿主会通过 `loreloop begin` 准备任务、读取相关知识，然后继续在当前会话中开发。

实现完成后，可以记录确定性检查并生成验收报告：

```bash
loreloop check <run-id> "测试通过" --command "pytest -q"
loreloop report <run-id>
```

completion、harvest 和知识策展始终由操作者明确决定。

## 知识里有什么

每条知识都是一条小断言，包含：

- 来源：源码位置、Git 提交、URL 或页面快照；
- 审核状态：draft、approved 或 rejected；
- 验证状态：unverified、verified 或 contradicted；
- 来源变化后的漂移状态。

SQLite 只是本地投影。提升信任的操作需要从防篡改证据链重放，链的凭据保存在项目目录
之外。代理仅仅修改数据库，不能让自己的笔记变成可信事实。

## 和常见方案的区别

| 方案 | 擅长什么 | LoreLoop 补充什么 |
|---|---|---|
| 会话记忆 | 保存最近对话和偏好 | 从代理进入项目之前就存在的代码和行为建立知识基线 |
| 代码检索 / RAG | 找文件和代码片段 | 带来源、漂移和审核状态的断言级知识 |
| Agent wrapper | 调度模型和工具 | 不采信代理自述的证据验收 |
| 团队文档 | 记录人类解释和决策 | 可检索、可验证、可退役、可复用的项目事实 |

LoreLoop 与这些工具配合使用，不要求替换它们。

## 当前支持

- 单仓库或多仓库代码反构
- 同源 Web 探索，以及可选的人工登录接管
- Codex、Claude Code、OpenCode 和 co-mind 当前会话集成
- 命令检查和浏览器验收
- 知识审核、拒绝、重开、替代和使用统计
- 不同信任域之间的只读项目联邦

OpenCode 支持交互式使用和无工具推理。由于其 CLI 暂时没有可验证的工作区沙箱，
`loreloop run --agent opencode` 仍然禁用。

## 公开证据

仓库中的 `eval/` 会测试反构、检索、真实编码任务和规模表现。当前小型基线包括：

- Codex 代码反构：14 条固定事实上 precision 1.00 / recall 1.00
- Claude 多语言反构：precision 0.82 / recall 0.90
- 固定查询扩展：固定检索集上 Hit@5 1.00 / MRR 1.00
- LoreLoop 任务组：checked-in Claude 任务夹具上 3/3

这些数字用于回归，不代表普遍领先。原始输入、评分代码、限制和尚未完成的真实参与者
可用性研究都在仓库中公开。

- [评测套件](eval/)
- [产品论证与证据](docs/product-thesis-and-evidence.md)
- [设计与实现](docs/design-and-implementation.md)
- [安全模型](SECURITY.md)
- [故障排查](docs/troubleshooting.md)

## 开发

```bash
git clone https://github.com/zhangguiping-xydt/loreloop
cd loreloop
uv sync --frozen --all-extras
uv run --frozen pytest -q
```

更多信息见 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [RELEASING.md](RELEASING.md)。

## License

MIT
