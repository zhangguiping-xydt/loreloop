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
→ Capsule v2
→ directory or ZIP transport
```

默认交付命令是：

```bash
loreloop knowledge export --format package --output baseline.zip
loreloop knowledge replay baseline.zip
```

`--format docs` 是兼容别名。package/docs 未指定 `--output` 时默认生成 `baseline.zip`。
`--format audit` 是知识条目逐条审计文件，不是权威项目包。

## 2. 项目与仓库边界

合法项目拓扑有两种：

1. Git 根仓库 `.`，加零个或多个已声明成员仓库；
2. 非 Git 聚合根，加至少一个通过 `loreloop repo add` 声明的 Git 成员仓库。

根仓库、成员仓库和递归子模块必须全部处于干净提交态。快照绑定仓库别名、Git 根历史、
提交、树、索引形状、每个 blob 的 Git OID、长度和 SHA-256。检测和文档生成只读取已捕获的
Git blob，不读取工作树文件。同一个物理 Git common directory 不能以 peer、submodule 或
linked worktree 等多个别名重复进入项目快照；事实去重不能跨仓库别名丢失成员仓库证据。

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

Markdown 是给人和编码代理共同阅读、评审、Git diff 与引用的交付界面，不生成 DOCX。
机器闭包由 `.loreloop-export.json` 中的 SemanticCore、完整 AST、适用性决策和摘要证明。
规范化项目名属于 SemanticCore 的摘要输入，不能从待验证 AST 标题反推；任何不在冻结路由
矩阵中的合法枚举记录都必须使导出和重放失败，不能以零 gap 静默丢弃。

六份核心文档必须承担不同职责，不能仅修改标题后重复同一张接口表：功能清单按证据化能力域
汇总；需求规格只收录需求材料和实施约束；系统架构表达仓库、依赖、配置和部署边界；详细设计
按仓库与源文件组织实现；用户手册只收录明确 UI/CLI/角色流程；验收规格只收录验收条款和测试
证据。证据不足时必须在对应文档明确声明不能用于完整需求、操作交付或正式验收，不能用“接口
存在”替代业务结论。

Markdown 明细必须直接显示仓库、路径和行号，避免多仓库同名配置、模块或接口看似冲突；完整
逐条证据身份继续保存在 Capsule，Markdown 只保留按仓库汇总的证据覆盖，避免在每份文档重复
大段证据索引。数据库内联 `KEY`/`INDEX` 必须投影为索引，不得伪装成字段。

## 4. 可移植包与信任模式

- 默认 ZIP 是确定性、扁平、可直接交付的传输包；目录形式继续兼容。
- 无密钥重放必须在没有源码、SQLite 和 LoreLoop 密钥时验证 AST、Markdown 与 Capsule 闭包。
- 重放必须从 Capsule 内的 SemanticCore 重新构建适用性和完整 DocumentSet，并逐字节比较 AST；
  旧的弱 v2 Capsule 必须明确要求重新导出，不能降级接受。
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

当前确定性检测覆盖 Python、TypeScript/JavaScript、Java/Kotlin、Go、Rust、C#、SQL、
SQLAlchemy、Django ORM/migration、Alembic、Prisma、TypeORM、OpenAPI/Swagger、GraphQL、
protobuf、Docker、Compose 与 Kubernetes。测试、fixture、snapshot 和 generated 文件保留在
源码快照覆盖中，但不作为产品语义。

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
