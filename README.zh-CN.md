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

如果正在开发本地源码，请显式安装当前 checkout，避免旧的全局命令遮蔽正在测试的代码：

```bash
uv tool install --force --editable '/当前/loreloop/源码绝对路径[web]'
```

如果命令中可用的 agent 或选项与源码不一致，运行 `type -a loreloop` 和
`loreloop ingest --help` 检查实际调用的是哪个运行时。

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

这条命令也是项目级 Skill 的升级入口。重复执行不会重置信任域或删除项目文件，只会校验现有状态，
并用当前已安装的 LoreLoop runtime 刷新受管的 `.claude/skills` 和 `.agents/skills` 副本。各宿主
集成会在新会话第一次使用 LoreLoop 时执行一次，因此旧项目不会长期保留过期的导出流程。

从代码建立第一版知识：

```bash
loreloop ingest --from code .
loreloop knowledge review
```

需要交接、熟悉项目或基于旧系统继续开发需求时，可以直接从各成员仓库的干净提交态生成可交付的
权威项目包。项目根目录本身可以不是 Git 仓库，只要成员仓库已经通过 `loreloop repo add` 声明。
默认过程不调用 Agent，也不读取 SQLite 或密钥：

```bash
loreloop knowledge export \
  --format docs \
  --output baseline \
  --project-name your-project \
  --requirements docs/requirements.md
```

默认仍使用干净提交态。正在开发、初始化刚生成了 `.agents/`、`.claude/`、`.opencode/` 或项目本身
还有未提交源码时，不需要为了导出而让 Agent 自动提交；可以直接固化当前工作树：

```bash
loreloop knowledge export --format docs --output baseline --working-tree
```

工作树基线包含 staged、unstaged 和未被忽略的 untracked 文件，绑定当前 HEAD、独立 Source Tree、
文件摘要和状态摘要，不修改真实暂存区，也不会运行仓库自定义的 clean filter。生成的每份文档都会
明确标记“可验证工作树快照”，不会冒充已经提交的发布态。严格模式报错会直接列出脏文件并提示该
命令；Codex、Claude Code、OpenCode 和 co-mind 的 LoreLoop Skill 也会在用户要求“当前项目基线”
时直接采用该模式，而不是要求用户先提交。

受支持的文本源码会优先按严格 UTF‑8 解码，失败后受控回退到 GB18030（同时覆盖兼容 GBK 的
文件）。主体仍是 UTF‑8、但少量字节损坏的历史文件可以进入有界恢复模式；损坏行产生的候选事实
会被丢弃，并在详细设计中形成“源码解析覆盖缺口”。无法安全恢复的文件只记录覆盖缺口，不参与
语义推断。LoreLoop 始终保留并摘要绑定仓库中的原始字节，生成基线不需要批量修改或转码源码。

默认产物是可以直接打开、全文搜索和 Git diff 的 `baseline/` 目录：

这些是便于评审、Git diff 和编码代理直接读取的 Markdown，不会生成 Word/DOCX；机器重放使用
包内的 Capsule JSON。

```text
baseline/
├── your-project-功能清单.md
├── your-project-需求规格.md
├── your-project-系统架构.md
├── your-project-详细设计.md
├── your-project-用户手册.md
├── your-project-验收规格.md
├── your-project-接口契约.md      # 有明确接口证据时生成
├── your-project-数据库设计.md    # 有明确表结构证据时生成
└── .loreloop-export.json         # SemanticCore、完整 AST 和内容摘要
```

固定生成六份核心文档；接口和数据库文档只在源码有明确证据时生成。当前检测覆盖 Python、
TypeScript/JavaScript、Vue SFC、Java/Kotlin、Go、Rust、C#、SQL、SQLAlchemy、Django ORM、
Prisma、TypeORM、常见 migration、OpenAPI/Swagger、GraphQL、protobuf、Docker、Compose 和
Kubernetes。受支持语言的测试文件只投影为验收规格中的测试证据，不会混入功能清单或详细设计。
终端会列出各仓库文件数、检测器覆盖、事实数量和未语义解析的文件类型。
规范化后的 `--project-name` 也属于 SemanticCore 身份，因此文件名、AST、Markdown 和 package ID
来自同一条确定性投影链，不是可以脱离证据单独修改的标签。

`.loreloop-export.json` 可以在没有源码、数据库或密钥的机器上证明整套文档没有缺失或篡改：

```bash
loreloop knowledge replay baseline
```

基线目录可以直接检索，也不会导入当前项目数据库：

```bash
loreloop knowledge search "公积金比例" --package baseline
```

检索只索引 Capsule 已验证的人类可见 Markdown，并按章节内的段落、列表和表格块建立临时
BM25 索引，而不是把每一行拆成孤立知识。结果仍定位到原 Markdown 文件和章节，摘要优先显示
真正命中的可见事实；Mermaid 图形语法不会污染召回。默认路径不需要模型、向量库或数据库。

当提问与项目术语不同，可以显式提供有界的同义词、翻译、缩写和可能的代码标识符：

```bash
loreloop knowledge search "公积金比例" \
  --package baseline \
  --expand "住房公积金 缴存比例 provident fund contribution ratio HPF hpfRatioConfig"
```

扩展词只影响检索排序，不会写入基线、不会作为项目知识展示，也不能提升结果的信任等级。Codex、
Claude Code、OpenCode 和 co-mind 可以在当前宿主会话中生成这些词；包检索不会再启动一个 Agent。
如果结果完全依赖扩展词，CLI 会明确标记为低置信度候选，提醒 Agent 回到命中文档和源码核对。

Web 探索结果默认仍留在可持续更新的知识库中。需要把运行时页面事实写回交付基线时，只有同时经过
人工批准和浏览器验证的当前 Web 条目会被纳入：

```bash
loreloop ingest --from web https://app.example.com --headed

# 把已捕获页面转成可评审、可重放的测试候选。
loreloop web test generate
loreloop web test review
loreloop web test approve <scenario-id>
git add tests/loreloop/web/<scenario-id>.json
git commit -m "add governed web test"

# 在本地或 CI 重放与证据链批准摘要完全一致的文件。
loreloop web test run --all
loreloop web test export --format playwright --output tests/playwright/generated

# Web 知识条目仍走独立的知识策展与复核流程。
loreloop knowledge review --status draft
loreloop knowledge approve <entry-id>
loreloop knowledge verify <entry-id> --headed
loreloop knowledge export \
  --format docs \
  --output baseline \
  --include-web \
  --force
loreloop knowledge replay baseline
```

`.loreloop/web-tests/candidates/` 中的文件只是私有、未受信任的评审材料。批准动作会把精确场景发布到
`tests/loreloop/web/` 并把摘要绑定到证据链；只有与链上批准摘要一致的文件可以执行。Playwright
文件是派生产物，不是权威源。场景默认只读；录制或重放写风险流程都必须显式使用
`--allow-writes`，密码、token、secret、API key 字段不会被录制。每次重放的 trace、终态观察、
断言和结果都会进入证据链；使用 `--include-web` 导出时，最近一次受治理结果也会作为验收事实进入
基线包。
非 Git 聚合工作区声明了多个成员仓库时，使用
`loreloop web test approve <scenario-id> --repo <repo-name>` 指定归属仓库；批准 JSON 会写入该
成员仓库，确保它能进入对应的提交态权威快照。

`--include-web` 会读取本地知识投影和防篡改证据链；draft、仅批准但未验证、仅验证但未批准、
contradicted、rejected、superseded 或内容摘要不匹配的 Web 条目都不会进入文档。Web 事实按种类
投影到需求、架构、功能、用户手册、接口或验收章节，Capsule 继续绑定全部内容。

如果还要证明“这份包由当前项目的本地信任域确认过”，导出时显式加 `--attest`，重放时加
`--trusted`：

```bash
loreloop knowledge export --format package --output baseline.zip --attest
loreloop knowledge replay baseline.zip --trusted
```

`--format docs` 默认写入可直接阅读的 `baseline/` 目录；只有需要传输或归档时才使用
`--format package` 生成 `baseline.zip`。`--format audit` 是另一种单文件逐条信任审计导出，
并不是权威项目文档包。

有需求文档时，把它提交到任一已声明仓库，然后在当前编码代理会话里建立任务边界：

```bash
loreloop begin "按需求文档给上传接口增加限流" \
  --requirements docs/upload-rate-limit.md
```

多仓库需求可写成 `repo:frontend/docs/requirements.md`。LoreLoop 读取的是需求文件在 `HEAD`
中的精确 Git blob，并把提交、SHA-256、正文和相关知识一起放回当前 Codex、Claude Code、
OpenCode 或 co-mind 会话；不切换聊天入口，也不启动嵌套 Agent。

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
- 六份核心 + 两份动态可选的权威项目文档、无密钥 Capsule 重放和可选本地证明
- ORM、接口契约、容器平台和多语言确定性源码检测
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
