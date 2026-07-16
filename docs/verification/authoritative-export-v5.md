# LoreLoop 权威项目包 v5：里程碑与可执行证明契约

状态：当前产品契约。本文替代历史上“固定十份文档、导出必须依赖本地密钥”的旧验证假设。

## 1. 交付目标

LoreLoop 从一个项目的提交态源码与显式需求材料构建同一条证明链：

```text
Git source snapshots
→ deterministic detectors
→ SemanticCore（含规范化项目身份）
→ typed document ASTs
→ Markdown
→ evidence-backed split-view Capsule v5
→ directory or ZIP transport
```

默认交付命令是：

```bash
loreloop knowledge export --format docs --output baseline
loreloop knowledge replay baseline
```

`--format docs` 生成可直接阅读的目录，未指定 `--output` 时默认使用 `baseline/`。只有明确需要
压缩交付物时才使用 `--format package --output baseline.zip`；package 未指定输出时默认使用
`baseline.zip`。
`--format audit` 是知识条目逐条审计文件，不是权威项目包。

基线可由 `knowledge search <query> --package baseline` 直接检索。搜索必须先在同一份不可变
文件快照上完成 Capsule replay，再对 SemanticCore Agent 视图执行有界 BM25；不得要求先导入
SQLite，也不得在验证后重新读取可能已变化的 ZIP。搜索结果必须返回规范归属的人类文档领域和
精确源码证据，且不得把 Agent 原子事实伪装成人类文档中的逐字陈述。

`--expand <terms>` 只允许提供有界的召回提示，如同义词、翻译、缩写和可能的代码标识符。扩展词
必须以低于原查询的权重参与排序，不得写入包、SemanticCore、Markdown、Capsule 或证据链，也不得
被呈现为知识或用于提升信任。宿主 Agent 可以在当前会话中生成扩展词，但包检索不得为此启动嵌套
Agent；无扩展时的确定性行为必须保持兼容。
如果候选全部仅由扩展词命中，CLI 必须明确报告低置信度；该标记不能改变 Capsule 信任状态。

## 2. 项目与仓库边界

合法项目拓扑有两种：

1. Git 根仓库 `.`，加零个或多个已声明成员仓库；
2. 非 Git 聚合根，加至少一个通过 `loreloop repo add` 声明的 Git 成员仓库。

根仓库、成员仓库和递归子模块必须全部处于干净提交态。快照绑定仓库别名、Git 根历史、
提交、树、索引形状、每个 blob 的 Git OID、长度和 SHA-256。检测和文档生成只读取已捕获的
Git blob，不读取工作树文件。同一个物理 Git common directory 不能以 peer、submodule 或
linked worktree 等多个别名重复进入项目快照；事实去重不能跨仓库别名丢失成员仓库证据。

受支持文本源码的确定性解码顺序是严格 UTF-8、GB18030，然后仅针对主体仍为 UTF-8 的有界损坏
执行 `utf-8-repaired` 投影。任何包含替换字符或锚定到损坏行的候选事实都必须丢弃；恢复状态、
替换数量和丢弃数量必须作为 `AnnotationRow` 进入 SemanticCore、详细设计和 Capsule。超过恢复
阈值的文件不得猜测编码，只记录“无法安全解码”覆盖缺口。所有路径始终绑定原始 blob 字节和
SHA-256，导出不得修改业务源码。

## 3. 文档集合

每个项目固定生成六份 Markdown：

1. 功能清单
2. 需求规格
3. 系统架构
4. 详细设计
5. 用户手册
6. 验收规格

源码存在明确接口证据时增加“接口契约”，存在明确数据库结构证据时增加“数据库设计”。因此
合法集合是 6、7 或 8 份，不允许用空占位文档凑数。

Markdown 是独立的人类阅读、评审、Git diff 与引用界面，不生成 DOCX。它必须把源码事实归纳为
运行单元、已实现能力、触发入口、角色边界、数据读写、UI 功能区和验收候选，并明确区分正式材料
与源码反构现状。Capsule SemanticCore 是独立 Agent 视图，保存精确原子事实、身份、字段和源码绑定。机器闭包由
`.loreloop-export.json` 中的 SemanticCore、确定性 pre-AST 摘要、适用性决策和 Markdown 摘要
证明。v5 不把 Agent 原子索引复制进人类 Markdown；重放从 SemanticCore 重建 AST 和人类语义
投影并核对摘要。schema v4 使用冻结的旧分离视图重放，v2/v3 的旧附录式包继续兼容。
规范化项目名属于 SemanticCore 的摘要输入，不能从待验证 AST 标题反推；任何不在冻结路由
矩阵中的合法枚举记录都必须使导出和重放失败，不能以零 gap 静默丢弃。

六份核心文档必须承担不同职责，不能仅修改标题后重复同一张接口表：功能清单跨仓库合并同名
入口域并单列 UI/CLI 概览；需求规格只收录需求材料和实施约束；系统架构表达仓库、依赖、配置和
部署边界；详细设计按仓库分层并跨仓库合并同名技术域；用户手册只收录明确 UI/CLI/角色流程；
验收规格只收录验收条款和测试证据。接口契约允许保留全部端点，但必须先提供接口域索引并折叠
领域明细。证据不足时必须在对应文档明确声明不能用于完整需求、操作交付或正式验收，不能用
“接口存在”替代业务结论。

Markdown 明细必须直接显示仓库、路径和行号，避免多仓库同名配置、模块或接口看似冲突；逐条
证据身份和字节范围继续保存在 Capsule，Markdown 使用可读的 `仓库:路径#行号` 并按仓库汇总
证据覆盖。数据库内联 `KEY`/`INDEX` 必须投影为索引，不得伪装成字段。

每个 Agent 原子事实只能有一个规范的人类文档领域归属。人类视图负责语义归纳、导航和交付判断；
Agent 视图负责精确类、方法、依赖、UI、测试、权限和约束检索。两边不共享同一套排版，但必须
来自同一 SemanticCore，并由稳定记录 ID、pre-AST、Markdown 摘要和源码绑定交叉证明。

真实规模门禁必须同时检查：正文不出现原子 `record_id`；概览按领域聚合；人类 Markdown 不复制
完整 Agent 原子索引；Agent 视图覆盖全部 SemanticCore 记录并具有唯一人类领域归属；需求为空时
不输出配置伪需求；Capsule 不内嵌重复 AST；至少使用接口名、类/方法名、数据库字段、测试名和带
受控扩展的中文业务词验证召回。

## 4. 可移植包与信任模式

- 默认日常产物是可直接浏览的目录；ZIP 是明确请求压缩交付时生成的确定性、扁平传输包。
- 无密钥重放必须在没有源码、SQLite 和 LoreLoop 密钥时验证 AST、Markdown 与 Capsule 闭包。
- 重放必须从 Capsule 内的 SemanticCore 重新构建适用性和完整 DocumentSet。v5 将重建 AST 和
  证据化人类语义投影并与 Capsule 摘要比较，再逐字节比较独立人类 Markdown；v4 使用冻结的旧
  分离视图渲染器，v2/v3 继续验证旧附录投影，
  v2 还必须验证内嵌 AST 与同一重建结果一致。任何格式都不能降级接受不完整的 SemanticCore。
- ZIP 不允许路径、目录、链接、重复文件、加密成员、未绑定的额外成员或超限解压内容。
- ZIP 与目录重放必须先受限读取 Capsule，再只加载 Capsule 精确绑定的 Markdown；Capsule、
  单文档、受管总量、压缩成员和压缩比均有生产上限。目录中真实的非受管操作者文件和目录
  可以保留并忽略，但符号链接和特殊节点必须拒绝。
- 当前上限为 Capsule 128 MiB、单 Markdown 32 MiB、受管总量 256 MiB。JSON 在构造 Python
  对象图前，以常量额外空间限制 400 万 values、300 万 object members、100 万 array elements、
  100 万 containers、单容器 10 万项、
  深度 128 和单字符串 8 MiB；ZIP 压缩包上限为 64 MiB，必须先读取为不可变的有界字节快照，
  再在同一快照上于 `ZipFile` 之前验证最多 16 个成员及 64 KiB central-directory 元数据，
  避免宽度型 JSON/ZIP 或前检后的原地改写先行耗尽内存。
- `--attest` 可选地把精确 Capsule 摘要与仓库身份/检出位置写入本地证据链。
- `--trusted` 除可移植闭包外，还必须验证相同本地信任链，并把仓库位置绑定到 Git common
  directory 的本机设备/inode 身份，以及每个仓库的精确 commit/tree/index/source snapshot；
  所有 Git 子进程必须清除调用者提供的仓库重定向环境，拒绝不同路径或原路径上的 clone/
  仓库内容替换。

## 5. 发布与恢复

- ZIP 首次发布使用不覆盖安装；若导出期间目标突然出现，必须保留操作者文件并失败。
- ZIP 的显式 `--force` 更新使用同文件系统原子替换，崩溃只能留下完整旧包或完整新包。
- 目录更新使用完整树发布、原子目录交换、同级 journal 与确定性恢复。
- journal 必须在 stage 创建和填充前持久化，使 staging 中断也能幂等清理；恢复完成前必须
  验证完整受管命名空间，不得接受额外旧 Markdown 或其他受管文件。
- 目录交换前后出现或修改的非受管操作者文件必须保留；首次安装期间突然出现的目录必须以
  no-replace 失败；旧 stage 已部分清理时恢复仍须幂等完成。
- `--force` 更新若改变规范化项目名，必须从旧 Capsule 安全恢复上一套受管文件名，并在同一
  事务中删除旧前缀文档；不属于旧/新 Capsule 的操作者 Markdown 继续保留。
- 输出目录或 ZIP 本身为符号链接时必须拒绝。

## 6. 检测器和诚实覆盖

当前确定性检测覆盖 Python、TypeScript/JavaScript、Vue SFC、Java/Kotlin、Go、Rust、C#、SQL、
SQLAlchemy、Django ORM/migration、Alembic、Prisma、TypeORM、OpenAPI/Swagger、GraphQL、
protobuf、Docker、Compose 与 Kubernetes。受支持语言的测试文件只生成 `TestRow` 并进入验收
规格，不进入功能、需求、架构或详细设计；fixture、snapshot 和 generated 文件保留在源码快照
覆盖中，但不作为产品语义。唯一受支持的 JSON 测试命名空间是 `tests/loreloop/web/*.json`；文件
必须通过严格 Scenario schema，普通业务 JSON 即使位于其他 tests 目录也不能被误识别。

`--include-web` 是显式混合来源模式。它只接受当前内容摘要同时具有链上人工批准和浏览器验证、且
未 contradicted/rejected/superseded 的 Web 条目。条目以有界合成 evidence blob 进入同一
SemanticCore，并按 kind 路由到既有文档族；默认导出仍完全不读取 SQLite 或本地密钥。混合包的
无密钥 replay 证明内容闭包，`--attest`/`--trusted` 继续用于证明本地信任域曾确认精确 Capsule。
同一模式还可纳入链上最近一次与批准 Scenario digest 匹配的 `web_test_executed`：提交态 Scenario
形成 `TestRow`，执行结果形成 `WebAcceptanceRow`，两者都必须有对应合成 evidence blob，不能只
修改 Markdown 文案。

正式证明必须包含一条真实 Chromium 的连续 Web E2E：从本地 HTTP 应用执行 CLI Web ingest，经过
Scenario generate/review/approve、Git commit、真实重放、Playwright 导出、知识人工 approve 和
浏览器 verify，再以 `--include-web` 导出、无密钥 replay，并在没有 `.loreloop` 状态的目录中直接
检索 ZIP。验收规格和 Capsule 必须同时包含 `loreloop-web` 测试定义及最近执行结果。测试可以使用
确定性的本地推理适配器消除外部模型波动，但不得替换
浏览器、KnowledgeStore、证据链、SemanticCore、Capsule、归档或检索实现；Playwright/Chromium
缺失必须让该证明门禁失败，而不是 skip。

导出必须报告：仓库数、提交态 blob 数、检测文件数、排除数、检测器分布、事实数、文档数，
以及未语义解析的主要后缀。没有证据的业务结论不得由模板或模型补写。

OpenAPI/Swagger 内容识别必须要求根对象上的规范版本标记；业务配置中嵌套的 `swagger`/
`openapi` 普通命名空间，即使值恰好是 `2.0`/`3.x`，也不能触发严格 OpenAPI 解析。

## 7. 里程碑

### M1：可实际运行的源码反构

完成条件：单仓库和非 Git 聚合多仓库均能生成 6/7/8 文档；需求材料可进入需求与验收文档；
真实项目错误包含仓库别名和文件路径；不调用外部 Agent。

### M2：可交付 Capsule 包

完成条件：ZIP 和兼容目录均可重放；缺失、增加、路径、重复、AST、Markdown、摘要等变异全部
失败；ZIP 字节在相同输入和运行时下可重复。

### M3：可信重放与崩溃恢复

完成条件：可选 attestation/`--trusted` 闭环通过；仓库 clone 替换失败；ZIP 竞争写入不覆盖；
目录崩溃恢复在本次环境的 XFS 与 ext4 上通过。

### M4：全量证明与双审

完成条件：在冻结提交的干净本地 clone 中运行全量测试、Ruff、Bandit、wheel、M1–M3 专项门禁
和大仓库 Dogfood；Dogfood 必须至少包含 5,000 个提交态文件、生成不少于 50 MiB 的展开包，
其提交必须可从公开 GitHub HTTPS `origin` 的一个显式 branch/tag ref 到达。runner 必须使用
空 HOME、禁用 credential helper 的匿名 `git fetch` 获取该 ref，再以 Git ancestry 验证固定提交；
不能信任本地伪造的 remote-tracking ref。Dogfood 参数、公开远端
身份和保留 artifact 是 `passed` manifest 的必需条件；保存命令
日志、摘要、全部 LoreLoop Python 运行时源码哈希与环境。测试门禁必须使用白名单环境，清除
调用者的 `PYTEST_*`、`PYTHON*`、`COVERAGE_*` 等测试选择/插件变量，禁用未知 pytest 插件
自动加载，并保存 `pytest --collect-only` 节点清单及摘要；两个独立只读审查者针对同一契约与
同一实现提交给出无 Critical/Major 的结论。

## 8. 唯一执行入口

冻结实现提交后运行：

```bash
python verification/authoritative_export/run.py \
  --output verification/authoritative_export/results/<commit> \
  --dogfood-repo /path/to/large/clean-or-dirty-git-repository \
  --filesystem xfs=/tmp \
  --filesystem ext4=/media/vdc
```

脚本从指定提交创建干净本地 clone；Dogfood 也从其提交创建干净 clone，因此操作者的工作树和
本机 `uv.lock` 不参与结论。runner 必须用 `findmnt` 核对并记录真实 XFS/ext4 mount、设备和
FSTYPE，不能信任调用者标签。每个命令的 stdout/stderr 写入独立日志，`manifest.json` 记录
提交/tree、契约哈希、全部权威闭包源码哈希、命令、退出码、耗时和日志 SHA-256。wheel 必须
安装到隔离环境执行 export/replay 冒烟；wheel 与 Dogfood ZIP 保留在结果目录并记录 SHA-256。
任一必需门禁失败时脚本非零退出。

## 9. 当前环境的锁文件例外

操作者工作树的 `uv.lock` 可能被企业镜像工具改写为 Artifactory，因此在脏工作树直接运行完整
测试时，公开 PyPI 注册表检查会有且只有这一项失败。该本地文件不得修改或提交。正式证明必须
在冻结提交的干净 clone 中执行；提交态 `uv.lock` 必须只包含 `https://pypi.org/simple`。
