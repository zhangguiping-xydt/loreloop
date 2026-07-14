# LoreLoop 设计与实现说明

> 截至 2026-07-12。本文描述 LoreLoop 当前的产品边界、核心模型、安全语义和实现结构。

---

## 1. 项目定位

**LoreLoop = 知识治理 + 编码代理委托 + 证据验收。**

LoreLoop 是独立、可开源的本地知识闭环,包名和 CLI 均为 `loreloop`。它不替代
Claude Code/Codex/OpenCode/co-mind 写代码,而是在代理执行前后补上三段能力:

1. **前置知识治理**:把代码、网页、人工输入等来源转成可溯源、可策展、可验证的结构化知识。
2. **中间委托执行**:把任务交给受支持宿主,同时注入按信任分级的上下文包。
3. **后置证据验收**:用链上记录、浏览器观察工件和人工/机器检查来判定 run 是否可接受,再把验收通过的事实回流为知识。

产品内核是本地 CLI,用户入口优先复用已经打开的 Claude Code/Codex/OpenCode/co-mind 会话。Companion
skill 在当前会话中调用 CLI,而不是要求用户切换到 LoreLoop 聊天入口。没有 Web 服务、
账号系统或远端状态。依赖按需引入:浏览器能力当前通过可选 `playwright` extra 提供。

### 非目标

- 不提供托管式 Web 驾驶舱、账号系统或云端同步。
- 不直接调用模型 API;复用操作者已经配置好的编码代理 CLI。
- 不实现 MCP 接入。
- 不自动 approve/reject/supersede 知识。
- 不自动登录或自动化凭据输入。

### 四条不变式

1. 正式验收断言由人写、人录;代理最多起草建议。
2. 裁决以证据链为准,不采信代理对自己工作的自述。
3. completion、harvest 和策展只在操作者明确授权后通过 CLI 触发;代理不能自行决定。
4. 编码代理对知识库只读;approve/reject/supersede 是操作者行为。

### 设计原则

这些原则是 LoreLoop 区别于普通 agent wrapper 的核心:

- **编码交给代理,裁决留给证据系统**:Claude Code/Codex 负责执行,LoreLoop 负责提供受治理的上下文、记录委托边界、生成可复审验收。
- **知识不是文档堆,而是带信任状态的事实表**:每条知识都必须有 source、kind、trust 和可重放的背书语义。
- **LLM 只产出候选,不能授予信任**:反构结果 born-draft;只有人工策展、浏览器验证或链背书 harvest mint 能提升信任。
- **根治优先,不做末端关键词兜底**:输入缺失、证据缺失、链损坏等问题在上游边界拒绝,不依赖 LLM 输出特定措辞来补救。
- **信任与展示分离**:SQLite 是缓存和展示材料;真正的强信任由证据链按当前内容 digest 重放得到。
- **验收必须可复审**:报告不仅显示 PASS/FAIL,还要能追溯到 completion、check、artifact、页面快照和链哈希。
- **自动化不替代策展判断**:是否 approve、reject、supersede、重新背书,都保持为操作者行为。

---

## 2. 仓库结构

```text
loreloop/
├── src/loreloop/
│   ├── cli.py                  # CLI 命令入口
│   ├── agents.py               # 支持宿主的 subprocess 适配
│   ├── companion.py            # 项目与 OpenCode companion 安装
│   ├── paths.py                # .loreloop/、树外 key 与 registry 路径
│   ├── knowledge/
│   │   ├── model.py            # Entry/Source/Trust/Link 模型
│   │   ├── store.py            # SQLite 存储
│   │   ├── endorsement.py      # 证据链背书与信任重放
│   │   ├── code_reverse.py     # 代码反构
│   │   ├── repos.py            # 多代码库声明、locator 与 drift
│   │   └── harvest.py          # 验收后知识回流
│   ├── delegate/
│   │   ├── context_pack.py     # 上下文选择与渲染
│   │   ├── expand.py           # 有界查询扩展
│   │   └── runner.py           # 委托执行与 trace 记录
│   ├── evidence/
│   │   ├── chain.py            # HMAC 证据链
│   │   └── artifacts.py        # 内容寻址观察工件
│   ├── report/
│   │   └── acceptance.py       # 验收评估与报告渲染
│   ├── federation/
│   │   ├── registry.py         # 用户级信任域注册表
│   │   └── reader.py           # 外域只读验证、检索与导入
│   └── webexplore/
│       ├── actions.py          # 受限交互脚本 DSL
│       ├── browser.py          # Browser/Observation 抽象
│       ├── explorer.py         # Web 探索循环
│       ├── web_reverse.py      # Web 反构
│       └── verify.py           # 浏览器验证
├── eval/                       # 可复现质量、检索、任务与规模评估
└── tests/                      # 单元、CLI、验收、安全语义测试
```

### `.loreloop/` 工作目录

`.loreloop/` 位于项目树内,编码代理通常可以写它,因此它只作为缓存、展示和材料目录使用,不能单独承载安全语义。

```text
.loreloop/
├── knowledge.db            # SQLite 知识库
├── evidence.jsonl          # 证据链记录
├── evidence.lock           # 跨进程 append 锁
├── evidence/artifacts/     # 浏览器/命令/脚本工件
└── runs/*.jsonl            # 委托 trace
```

POSIX 上这些状态目录会收紧到 0700,状态文件收紧到 0600;状态根、DB、链、锁、
artifact 及关键中间目录拒绝符号链接。Windows 上 mode bit 不是 ACL,仍依赖用户目录 ACL。

证据链 HMAC key 和 head 承诺位于项目树外:

```text
~/.loreloop/keys/<workdir-hash>.key   # HMAC key,0600
~/.loreloop/keys/<workdir-hash>.head  # 最新 (index, chain_hash) 承诺
```

正常初始化会把“项目绝对路径 → operator-owned trust 目录”的连接写入
`~/.loreloop/trust-locations.json`,后续 Codex、Claude Code 和终端会话自动解析,
无需用户导出环境变量或管理 key 文件。注册表只是 locator,不是信任授权:
候选目录仍必须验证既有证据历史,错误或被篡改的映射只能 fail closed,不能授予信任。
已有历史但原 trust material 缺失时绝不生成替代 key。`LORELOOP_KEY_DIR` 仅保留为
高级部署/迁移覆盖,测试环境也使用该变量隔离真实 home。

SQLite 使用 `PRAGMA user_version` 和有序 migration。打开旧 schema 时先生成
`knowledge.db.schema-v<old>.bak`,再在单个 `BEGIN IMMEDIATE` 事务内升级;失败回滚。
高于当前版本的 schema 在任何写入前被拒绝,降级通过恢复对应备份完成。

---

## 3. 知识模型

知识条目由 `Entry` 表示,核心字段包括:

- `id`:稳定标识。
- `title`:短标题,用于展示和检索权重。
- `content`:一条断言级事实。
- `kind`:单轴分类。
- `source`:结构化溯源。
- `trust`:人工策展和机器验证两条信任轴。

### 3.1 Kind 单轴分类

`Kind` 只表达条目事实类型:

- `requirement`
- `interface`
- `architecture`
- `behavior`
- `constraint`
- `acceptance`

它不混入来源、信任状态或生命周期。

### 3.2 Source 结构化溯源

`Source` 包含:

- `channel`: `code` / `web` / `image` / `manual` / `evidence`
- `locator`:源位置,例如 `src/api.py@<commit>` 或 URL
- `snapshot_ref`:锚点,例如 git commit 或页面快照哈希
- `symbol`、`line_start`、`line_end`、`excerpt`:可选的精确代码证据位置

新鲜度由锚点判断。代码条目通过锚定 commit 与当前树对比识别 drift;Web 条目通过页面观察快照判断 drift。

### 3.3 Trust 双轴状态

`Trust` 分为两条独立轴:

- `curation`:人工策展状态
  - `draft`
  - `approved`
  - `rejected`
- `verification`:机器验证状态
  - `unverified`
  - `verified`
  - `contradicted`

强证据定义为:

```python
is_strong_evidence() = curation == approved or verification == verified
```

状态机约束:

- draft 可进入 approved/rejected。
- approved 可再次 approved,用于对当前内容重新背书。
- rejected 可回到 draft。
- verification 可以从 unverified 进入 verified/contradicted,但不能回退到 unverified。

矛盾和取代关系不写进状态字段,而由 links 表表达。

---

## 4. 证据链

`EvidenceChain` 是追加式 JSONL 证据链,每条记录包含:

```text
index
ts
event
payload
prev_hash
chain_hash
signature
```

计算规则:

```text
chain_hash = sha256(canonical_json(prev_hash, index, ts, event, payload))
signature  = hmac_sha256(project_key, chain_hash)
```

### 4.1 树外 key 与 head 承诺

HMAC key 不在项目树内,避免编码代理直接重签篡改后的证据链。每次 append 后,链尾 `(index, chain_hash)` 写入树外 head 文件。这样不仅能检测记录内容修改,也能检测尾部截断。

### 4.2 Append 语义

append 在 flock 锁内执行:

1. 读取当前链。
2. 完整校验 index、prev_hash、chain_hash、signature、head。
3. 计算新记录。
4. 追加 JSONL 并 fsync。
5. 原子更新 head 并 fsync 目录。

如果当前链已损坏或被截断,append 拒绝扩展它。

### 4.3 Verify 与 head 自愈

`verify()` 完整校验证据链。若链本身有效,但 head 缺失或落后于链尾,`verify()` 会在锁内重读并推进 head 到已签名链尾。

这个自愈只背书已经持有有效 HMAC 的记录;如果 head 指向的记录在链中不存在,仍然视为截断错误。

### 4.4 遗留树内 key

如果发现旧位置 `.loreloop/evidence.key`,而新的树外 key 不存在,CLI 拒绝继续并提示操作者手动选择迁移或重新开始。key 是否可信不能由程序静默决定。

---

## 5. 链背书与信任重放

SQLite 存在于项目树内,因此 trust 列只是缓存。真实信任由证据链事件重放得到。

### 5.1 Entry digest

每次提升信任都会把当前条目的内容摘要写入链上:

```text
entry_digest = sha256(canonical_json(
  id,
  title,
  content,
  kind,
  source.channel,
  source.locator,
  source.snapshot_ref,
))
```

摘要不包含 trust 字段。trust 由链事件表达,摘要只 pin “信任授予给哪一条事实”。

### 5.2 强信任判定

当前实现有两类检查:

- `chain_endorsed_strong_ids(entries, records)`:当前行 digest 被链背书的条目。
- `unendorsed_strong_ids(entries, records)`:DB 自称 strong,但当前行 digest 没有链背书的条目。

因此:

- DB 被改成 strong 但链不背书 → 降级为 reference。
- 链背书的行被删除,或内容/来源出现没有 `entry_reingested`/harvest 溯源事件解释的
  digest 变化 → fail closed,停止委托并要求操作者恢复或重新反构。
- 有链记录解释的重锚条目可以继续作为 reference,直到重新 approve/verify。
- DB trust 缓存被改回 draft,但当前 digest 仍被链背书 → 仍作为 strong 使用。

### 5.3 链事件重放

`endorsed_strong_digests(records)` 重放以下事件:

- `curation_changed` 且 curation 为 `approved`:加入 approved digest。
- `curation_changed` 且 curation 非 `approved`:移除 approved digest。
- `entry_verified`:加入 verified digest。
- `entry_contradicted`:移除 verified digest。
- `knowledge_harvested.minted`:加入 verified digest。

`knowledge_harvested.reversed` 只作为溯源材料,不提升信任。代码再反构是 LLM 提取,不能继承人工或机器验证背书。

### 5.4 Supersede 与 reject

- `chain_superseded_ids(records)` 从链上重放被取代条目,删除 DB links 行不能复活旧条目。
- `chain_rejected_ids(records)` 从链上重放最新 curation 状态,被 rejected 的条目不会因为 DB curation 被改写而重新注入。

---

## 6. 委托执行与上下文包

当前会话准备与独立进程委托都由 `DelegateRunner` 和 `context_pack` 负责。

### 6.1 选择策略

上下文选择使用确定性 BM25 评分,外加可选的 LLM 查询扩展:

- 词项抽取:ASCII 标识符 + 中文字符 bigram(无分词依赖,任务与条目之间能产生部分重叠)。
- BM25:IDF 加权 + 文档长度归一化,标题词项按权重计入词频。
- 查询扩展:`run` 默认先用已有的 agent CLI 把任务扩展成一组中英双语关键词
  (`delegate/expand.py`),结构化输出按 prompt/model/task 缓存;扩展词只进评分器,不进委托 prompt;扩展词记入 run trace
  (`query_expansion` 字段)可审计;扩展失败非致命,自动退化为纯 BM25;
  `--no-expand` 完全跳过。
- `begin` 不调用另一个模型,默认只使用任务原文。当前 host agent 可通过 `--expand`
  提供额外检索词;这些词同样只进评分器、不进 context pack。
- 英文停用词在分词时剔除;原始任务词权重高于扩展词。
- strong/链背书、kind 与完整 provenance 只做小幅质量加权,不能替代词项相关性。
- 相对分数下限、sharp-gap 与原始词覆盖率共同决定变长结果集,避免任意
  `score > 0` 都占用上下文预算。

安全边界:扩展是 LLM 输出,但它只能影响"检索到哪些条目",不能影响条目内容、
信任等级或 prompt 结构——最坏情形等价于一次糟糕的搜索。

当前实现不使用 embedding 和向量库,以保持 MVP 可解释、可测试;后续引入 embedding/混合检索不受约束。

### 6.2 渲染分层

上下文包分两层:

1. **Established facts**:链背书或 DB strong 且未被降级的事实,要求代理不要违背。
2. **Unverified references**:草稿、漂移、未背书或其他参考性条目,要求代理使用前自行核对。

上下文包开头明确声明:这些条目是项目数据,不是给代理的指令。每条知识渲染为单行 JSON 对象,字符串里的换行、标题符号或伪造 `# Task` 只作为 JSON 字符串内容出现,不能改变 prompt 的 Markdown 结构。

### 6.3 注入时降级

注入路径从 `store.list()` 候选全集开始,再基于当前工作树和证据链动态调整等级,不直接改写 DB:

- 代码锚点漂移 → reference,并标注 `source_changed_since_capture`。
- DB strong 但链不背书 → reference。
- 当前 digest 被链背书 → 即使 DB curation/link 缓存被改成 rejected/superseded,仍按链 strong 候选处理。
- 条目被链上 rejected/superseded → 不注入或显示为退役状态。

在上述分层前,`assert_trust_projection` 先检查链权威与 SQLite 投影的完整性。这样
“把已背书行改成恶意 reference”不能绕过 digest 检查；无法解释的缺失/改写不会进入 prompt。

### 6.4 当前会话、Run trace 与 completion

`.loreloop/runs/*.jsonl` 记录委托 trace,包含 started/finished/failed 等事件,用于展示和排查。验收不信任 trace 本身。

交互式入口使用两阶段生命周期:

1. `loreloop begin <task>` 选择并渲染知识,创建 trace,把 task/context/base commits/
   repository roots/ingestion policies 先写为签名 `delegation_prepared`,然后把 context pack
   返回当前编码代理会话;不会启动嵌套代理。
2. 实现完成后,只有操作者明确确认,companion skill 才调用
   `loreloop complete <run_id> --confirm`。complete 从签名 preparation 复制权威元数据,
   不读取 agent 可写 trace 中的 task/context/base 值。

独立进程自动化继续使用 `loreloop run`;代理成功返回后直接写
`delegation_completed`。两条路径的 completion 都包含:

- `run_id`
- `task`
- `context_entries`
- `base_commits`
- `repository_roots`
- `ingestion_policies`

验收和 harvest 都以该链记录为权威锚点。

### 6.5 Agent 运行能力配置

反构、查询扩展和自由文本裁判会接触不可信源码/页面文本,但不需要项目工具。Claude/
co-mind 以 tools 关闭、无 session、空 setting sources/MCP 配置运行；Codex 以 read-only
sandbox、ephemeral、忽略用户配置/规则并在空临时目录运行；OpenCode 使用内联配置关闭
plugin 与工具、拒绝全部权限。实际代码委托使用显式 `acceptEdits`/`workspace-write`,不继承
bypass 模式。OpenCode 因缺少可验证的 workspace sandbox,暂不开放 headless delegation。

子进程环境删除 `LORELOOP_KEY_DIR`、registry 等操作者能力,并设置
`LORELOOP_AGENT_PROCESS=1`,使正常 `EvidenceChain.append` 拒绝签名。这是对合作型 CLI 的
能力缩减,不是抵御恶意二进制的 OS sandbox。另一个重要边界是:存储在本地不等于推理
在本地；宿主 CLI 仍可能把源码、页面观察和 prompt 发给其配置的外部 provider。

---

## 7. 浏览器观察、验证与工件

### 7.1 Observation

浏览器观察抽象为 `Observation`:

- `url`
- `title`
- `text`
- `forms`
- `links`
- `headings`
- `buttons`
- `nav`
- `snapshot_hash`

`snapshot_hash` 覆盖裁判实际读取的页面窗口:标题、可见文本和表单结构。
`headings`/`buttons`/`nav` 是结构化上下文,用于提高反构质量和审计可读性,但不进入
`snapshot_hash`;避免纯导航文案或按钮列表排序变化制造过多内容级 drift。

Playwright 观察在导航后等待 `networkidle`,滚动页面触发懒加载,再回到顶部读取页面。链接收集不只看
`a[href]`,还包括 `[role=link]`、`data-href`/`data-url` 和常见 onclick 导航。`observe()` 对
HTTP 4xx/5xx 响应报错,探索循环会把这些 URL 记为 skipped,不把错误页喂给知识提取。

`ingest --from web` 的初始种子来自三处:

- 用户给定 URL。
- same-origin 的 `sitemap.xml`/`robots.txt`。
- 代码中可静态识别的绝对路由字符串,作为实现视图对行为视图的补充。

### 7.2 ArtifactStore

浏览器观察会保存为内容寻址 JSON 工件:

```text
.loreloop/evidence/artifacts/<sha256>.json
```

工件类型包括:

- `page_observation`:浏览器终态观察。
- `interaction_script`:可重放动作脚本本体。
- `interaction_trace`:每一步执行结果、耗时、终态 URL/快照。

实现约束:

- 工件目录 chmod 0700。
- 工件文件 chmod 0600。
- 先写临时文件,再 chmod,最后 rename 到最终 hash 文件名。
- 读取时校验文件名 SHA 与内容 SHA 一致。
- artifact 引用必须是 64 位小写 hex,不能作为路径片段绕出目录。

### 7.3 确定性断言

`verify` 支持三种确定性断言:

- `contains:<text>`
- `absent:<text>`
- `title-contains:<text>`

确定性断言不经过 LLM。空 needle 被视为 malformed expectation,在浏览器打开前拒绝。

### 7.4 可重放动作脚本

`verify --script <actions.json>` 在验收前先按动作脚本到达交互后状态。脚本是 JSON 数据,不是程序:
没有变量、条件、循环或 eval。v1 只支持五个动作:

```json
{
  "version": 1,
  "base": "http://localhost:3000",
  "steps": [
    {"goto": "/products"},
    {"click": {"text": "Filter", "role": "button"}},
    {"fill": {"label": "Max price", "value": "100"}},
    {"select": {"label": "Sort", "option": "Price low to high"}},
    {"wait": {"text": "Filtered results"}}
  ]
}
```

脚本以 canonical JSON 做 SHA-256,脚本锚写作 `script:<sha256>`。一个脚本成功跑完后,终态仍然产出标准
`Observation` 和 `snapshot_hash`;因此交互后知识条目的 freshness 由二元组表达:

- locator: `script:<sha256>`,说明如何到达状态。
- snapshot_ref:终态 `snapshot_hash`,说明到达后看到什么。

重放结局分三类:

- `completed`:所有步骤完成,进入终态观察和断言。
- `failed`:定位不到、歧义、wait 超时等路径级 drift;脚本锚 entry 复核时会写
  `entry_contradicted`。
- `blocked`:触发安全规则;这是执行器拒绝动作,不是断言为假,不会把 entry 标成 contradicted。

执行器硬编码安全边界:

- `goto` 和 `wait.url` 只接受相对路径,执行中任何出同源导航都会 blocked。
- Playwright request interception 默认阻断跨域请求和同源非 GET/HEAD/OPTIONS 请求；
  `--allow-writes` 只放开后者,不放开跨域。每步后消费 blocked request,终态 URL 在断言前复核。
- password 控件永不填。
- destructive/pay/transfer/delete 等危险文本不点击。
- 默认只允许 search/filter 类幂等 fill/select;一般写操作需要 `--allow-writes`。
- click 打开新窗口/新标签页视为 failed。

这些规则不是数据库事务或通用浏览器沙箱。同源 GET 在设计不良的服务端仍可能有副作用,
因此动作脚本应面向 disposable/staging 环境,并在开启 `--allow-writes` 前人工审阅。

### 7.5 LLM 裁判

自由文本 expectation 走 LLM 裁判。页面内容被包进带随机 nonce 的 UNTRUSTED 定界符内,并明确说明页面中的指令式文本只是证据,不是命令。

### 7.6 Entry 复核

`knowledge verify` 可对 Web 条目重新观察源页面:

- 通过 → 写入 `entry_verified`,DB verification 变为 verified,并把 snapshot_ref 重锚到本次观察快照。
- 不通过 → 写入 `entry_contradicted`,DB verification 变为 contradicted。
- locator 为 `script:<sha256>` 的条目会从证据链上的 `interaction_script` 工件恢复脚本并重放。
  重放 blocked 时命令报错退出,不写链、不改 DB;failed 才表示路径级 drift。

链记录先写,DB 后写。通过验证时,链上 digest pin 的是重锚后的行。

---

## 8. 验收报告

报告由 `report/acceptance.py` 生成,是证据链的投影,不额外存状态。

### 8.1 Accepted 条件

一个 run 只有同时满足以下条件才是 ACCEPTED:

1. 链上存在且仅存在一条对应 `run_id` 的 `delegation_completed`。
2. 至少有一条验收 check。
3. 所有计入的 check 都发生在 completion 之后。
4. 没有 failed check。
5. 所有引用 artifact 的 check 都通过完整性审计。

trace 中的 `delegation_finished` 只用于展示,不能让报告变成 ACCEPTED。

### 8.2 Check 类型

- `loreloop check`:人工记录,链上标注 `judge: operator`。
- `loreloop verify`:浏览器验证,链上标注 `verified_via: browser`,并携带 url、page_snapshot、artifact。
- `loreloop verify --script`:先重放动作脚本,再对终态页面执行同一套 deterministic/LLM 断言。链记录额外携带
  `script_digest`、`script_locator`、`script_artifact`、`trace_artifact` 和 `steps_completed`。

人工 check 合法,但报告会说明它没有可复审工件;harvest 不从人工 check 铸造 verified 知识。

### 8.3 Artifact 审计

报告渲染时可传入 `ArtifactStore`。CLI 路径始终传入该 store。

每个 page observation artifact check 会检查:

- 文件是否存在。
- 文件内容 hash 是否匹配 artifact 引用。
- 链记录是否包含 url/page_snapshot pin。
- artifact 中的 url 是否匹配链上 url。
- artifact 中的 snapshot_hash 是否匹配链上 page_snapshot。

带 `script_digest` 的 check 还会检查:

- `script_artifact` 和 `trace_artifact` 是否存在且内容 hash 匹配。
- `script_artifact.type == interaction_script`。
- script artifact 中的 `script_digest` 和 canonical script digest 是否匹配链上 `script_digest`。
- `trace_artifact.type == interaction_trace`。
- trace artifact 中的 `script_digest` 是否匹配链上 `script_digest`。
- trace 中的 `final_snapshot` 若存在,必须匹配链上 `page_snapshot`。

任一失败都会让 run 变成 NOT ACCEPTED,并在报告中列出 integrity failure。

### 8.4 CLI 错误边界

`loreloop report <run_id>` 会在 trace 缺失时返回干净错误。trace JSON 损坏、缺少 `delegation_started` 或关键字段格式不对时,CLI 也以错误消息和非零状态退出。证据链校验失败同样由 CLI 捕获,不会打印 Python traceback。所有 action 使用真正的 argparse 子命令;预期失败统一输出一条 `error`、一条 `reason` 和一条可执行的 `next`。Ctrl-C 会写入 `delegation_interrupted`,普通代理失败写入 `delegation_failed`,两者都不会产生链上 completion。

---

## 9. Harvest 知识回流

`loreloop harvest <run_id>` 只处理 ACCEPTED run。

### 9.1 回流来源

harvest 产生两类输出:

1. **Browser-verified checks → verified acceptance entries**
   - expectation 来自人。
   - 页面由浏览器验证。
   - check 和 artifact 都在链上可审计。
   - 铸造出的条目 born-verified。
   - 普通页面 check 的 `Source.locator` 是 URL;脚本 check 的 locator 是
     `script:<sha256>`,snapshot_ref 仍是终态页面快照。

2. **Changed code → draft entries**
   - 从 `base_commit` 到当前 HEAD 的变更文件重新反构。
   - LLM 提取结果 born-draft。
   - 验收通过不自动给代码反构产物授信。

### 9.2 Base commit

变更范围以链上 `delegation_completed.base_commit` 为准,不读取 trace 里的 base commit。工作树有未提交源码改动时拒绝 harvest,避免把不可复现内容锚定到错误 commit。

### 9.3 链先行

minted 条目先在内存中计算出最终内容和 digest。`knowledge_harvested` 写链成功后,才把 verified 状态落到 DB。链写失败不会留下无背书 strong 行。
该链事件同时携带完整 minted 行作为恢复日志。若进程在链成功、DB 未完成之间崩溃,
重跑 harvest 只补写缺失且 digest 与签名事件一致的行,不重复追加事件;已完整落库的
run 仍返回 already harvested。

### 9.4 Review 与 demotion

harvest 会输出需要人工关注的集合:

- `unauditable_checks`:缺少可复审工件的 check,不铸造。
- `review`:同一页面上已有强条目,需要人判断是否仍成立。
- `stale`:源文件发生变化的既有代码条目。
- `demoted`:重锚后失去当前 digest 背书的强条目。

旧断言与新断言的取代关系不自动推断,由操作者通过 supersede 明确记录。

---

## 10. 反构管线

### 10.1 代码反构

`reverse_code` 从代码文件中提取断言级知识:

- 按文件和大小分批。
- CLI 在每个提取批次调用模型前向 stderr 报告批次序号、总批数和文件数,
  并在分类调用前报告该批断言数;`reverse_code` 通过可选结构化回调暴露进度,
  本身不强制输出。
- 提取与分类分为两个 LLM 步骤。
- 输出必须是合法 JSON。
- 条目 source 锚定当前 git commit。
- 输入按行编号并置于随机 nonce 的 untrusted-source 边界内。
- 每条断言携带 symbol/行区间/excerpt;首次输出的 excerpt 必须与源行实际匹配。
- 首次模型输出若未通过 JSON、路径、行号、symbol 或 excerpt 的同一套确定性校验,
  `code-extract-v3` 最多进行一次修复调用;失败原因放在独立随机 nonce 的
  untrusted 边界中。重试时若只有 excerpt 与已经验证有效的文件和行区间不匹配,
  系统使用实际源代码区间生成规范 excerpt;路径、行号、symbol、类型或其他校验
  再次失败时仍整批拒绝。
- 允许一个文件产出 0 条知识,不再用最低条数驱动模型凑数。
- 批内近重复断言做保守 Jaccard 去重。
- 新条目默认 draft/unverified。

### 10.2 Web 反构

`reverse_web` 从浏览器探索得到的页面观察中提取知识:

- 页面观察作为证据输入。
- 页面结构与文本以 JSON 放入随机 nonce 的 untrusted-page 边界。
- 条目 source 为 Web URL。
- snapshot_ref 为页面观察快照哈希。
- 新条目默认 draft/unverified。

### 10.3 去重

`KnowledgeStore.add` 做精确去重。代码来源的去重 locator key 只取文件部分,避免同一文件跨 commit 产生重复行;具体新鲜度由 snapshot_ref/locator 锚点表达。

---

## 11. Web 探索

`Explorer` 采用有界、可追踪的同源探索循环:

1. 从入口 URL、代码静态路由、robots/sitemap 和已观察链接生成同源种子。
2. 逐页导航,等待 network-idle 并滚动触发懒加载。
3. 记录页面文本、表单、标题、按钮、导航和语义链接。
4. 把新发现的同源链接加入有界队列。
5. 为开始、跳过、登录交接、页面观察和结束写入 JSONL trace。

约束:

- 同源限制。
- 最大页面数限制。
- 遇到登录墙时,headless 模式跳过;headed 模式把真实浏览器窗口交给人登录。
- 人工登录完成后读取浏览器当前页面,而不是重新打开旧登录 URL;随后继续探索该页面链接。
- 入口 URL 本身是登录页时同样触发交接。交接成功、放弃和观察失败都有独立 trace 事件。
- LoreLoop 不保存凭据,不自动提交登录表单。

---

## 12. Companion skill

LoreLoop 提供多宿主、同一本地核心的集成:

1. 仓库 marketplace 中的 Codex plugin 是全局入口。它携带 `$loreloop` skill 和本地
   LoreLoop 安装器；本地命令缺失时,已启用插件本身视为安装授权,插件直接从 GitHub
   Release 下载 `SHA256SUMS` 与版本化 wheel,校验后完成安装。插件不执行远端 installer script。
2. co-mind 复用 Claude-compatible marketplace/plugin bundle,但通过自己的 CLI 与
   `~/.icodemate/cli` 存储独立安装。
3. OpenCode 使用全局或项目 `.agents/skills` Skill 与 `/loreloop` command,不改
   `opencode.json`,也不引入常驻 plugin。
4. `loreloop init` 在具体项目中按已检测宿主安装共享 Skill；Claude/co-mind 共用
   `.claude/skills`,Codex/OpenCode 共用 `.agents/skills`,避免重复副本。

skill 的作用是让编码代理把 LoreLoop 作为当前会话背后的本地引擎:

- 当前会话优先调用 `loreloop begin`,不使用 `loreloop run` 启动嵌套代理。
- 阅读和尊重 context pack。
- 把 established facts 当作约束。
- 把 references 当作需核对的信息。
- 完成工作后给操作者起草可验证的验收断言。
- complete、验收、harvest 和策展都要求操作者针对具体 run/entry 明确授权。

两个 companion skill 使用同一份协作契约。它们可以在同一 host 会话里代操作者执行
CLI,但不能把自身判断当作授权,不能自行签 completion、harvest 或策展。

LoreLoop 发行不依赖 PyPI:tag workflow 把版本化 universal wheel、POSIX/PowerShell
installer、`SHA256SUMS`、SBOM 和 provenance 一并发布到 GitHub Release。PyPI 只是
额外生态渠道。installer 只安装通过 release checksum 的 wheel;Web 能力仍按需安装
Playwright 与 Chromium。

---

## 13. CLI 命令面

主要命令:

```text
loreloop doctor
loreloop init [--skill|--no-skill]
loreloop claude (install|status|uninstall)
loreloop codex (install|status|uninstall)
loreloop opencode (install|status|uninstall)
loreloop comind (install|status|uninstall)
loreloop ingest --from code <path> [--agent claude|codex|opencode|co-mind]
loreloop ingest --from web <url> [--headed] [--max-pages N]
loreloop begin <task> [--expand <terms>] [--requirements <path>]...
loreloop complete <run_id> --confirm
loreloop run <task> [--agent claude|codex|co-mind]
loreloop check <run_id> <check> (--pass|--fail) [--detail <text>]
loreloop check <run_id> <check> --command <argv-string> [--timeout <seconds>]
loreloop verify <run_id> <url> <expectation> [--headed]
loreloop verify <run_id> <base-url> <expectation> --script <actions.json> [--allow-writes] [--headed]
loreloop report [run_id]
loreloop harvest <run_id> [--agent claude|codex|opencode|co-mind]
loreloop knowledge list [--stale]
loreloop knowledge export [--stale] [--format audit] [--output <file>]
loreloop knowledge export --format docs --output <directory> [--project-name <name>] [--requirements <path>]... [--force] [--attest]
loreloop knowledge replay <directory> [--trusted]
loreloop knowledge approve <entry_id>
loreloop knowledge reject <entry_id>
loreloop knowledge supersede <new_id> <old_id>
loreloop knowledge verify <entry_id> [--headed]
loreloop knowledge usage
loreloop repo add <repo_path> [--name <repo_name>]
loreloop repo list
loreloop repo remove <repo_name>
loreloop project add <project_path> [--id <project_id>] ...
loreloop project list
loreloop project remove <project_id>
```

文件路径相关的 run id 使用严格正则校验,避免路径穿越。

---

## 14. 安全语义

LoreLoop 的威胁模型是 **honest workstation**:

- 本机、文件系统、OS 用户可信。
- 防御对象是有项目目录写权限的编码代理。
- 不防御能写 `~/.loreloop/keys/` 的本地攻击者、恶意代理二进制或 root。

### 14.1 防御点

| 威胁 | 机制 |
|---|---|
| 页面 prompt injection | 确定性断言不过模型;LLM 输入使用随机 nonce untrusted 定界符;context pack 声明数据非指令且条目按单行 JSON 渲染 |
| 证据篡改 | HMAC 链;树外 key/head;append 前验链;artifact 内容寻址;url/snapshot pin;check 时序;单 completion 约束 |
| 交互脚本失控 | DSL 无条件/循环/eval;同源限制;password 永不填;危险点击 blocked;写操作默认禁用;script/trace artifact 纳入报告审计 |
| 信任洗白/压制 | digest 绑定;链重放;DB-only strong 降级;链背书行缺失/无解释改写 fail closed;链 strong 不被 DB draft 压制;rejected/superseded 链权威 |
| Agent 越权 | inference 临时 cwd + no-tools/read-only;delegation 显式非 bypass;删除 key/registry 环境;agent marker 拒绝正常签名 API |
| 凭据误捕获 | 不自动登录;headed 登录交人;artifact 0600/0700;命令 stdout/stderr 脱敏;`.loreloop/` 加入 gitignore;eval transcript 保存前脱敏并截断 |

### 14.2 设计取舍

- `run` 不会每次重新打开浏览器验证 Web strong entry;它信任最近一次验证并打印提醒。
- 页面 snapshot_hash 覆盖的是裁判实际读取的有界窗口,不是完整 DOM。
- 交互脚本 `blocked` 表示执行器拒绝动作,不是知识断言为假;`knowledge verify`
  对脚本锚遇到 blocked 会报错退出,不改信任状态。
- operator check 是有效人工背书,但没有机器可复审工件;报告会标注,harvest 不铸造。
- `check --command` 不经过 shell,记录 argv、退出码与有界输出 artifact;退出码 0
  且 artifact/链 pin 一致时可回流为 evidence-channel verified acceptance。证据同时绑定各
  repo 的 HEAD 与 working-tree digest；harvest 时状态不同则拒绝。stdout/stderr 先脱敏。
- 本地存储不代表本地推理；provider 数据处理与恶意 agent 二进制均在 honest-workstation
  威胁模型之外。
- append 与 head 更新之间如果发生崩溃,最新记录在下一次 verify 之前没有 head 截断保护;下一次 verify 会关闭这个窗口。

---

## 15. 测试策略

测试目标是把关键安全语义写成可执行约束。当前测试覆盖:

- 证据链 append/verify/head/truncation/key 权限。
- artifact hash、状态目录 0700/文件 0600、符号链接和路径引用校验。
- deterministic expectation 与 LLM verifier JSON 解析。
- chain-backed trust 与 DB trust cache 的不一致场景。
- rejected/superseded 链重放。
- context pack strong/reference 分层。
- trace 不作为验收权威。
- completion/check 时序。
- duplicate completion 拒绝。
- artifact 缺失、篡改、掉包、缺 pin 的报告降级。
- harvest 链先行、幂等、dirty tree 拒绝、unauditable check 不铸造。
- command evidence 的 repo-state 绑定、fail→pass 最新结果语义和秘密脱敏。
- inference/delegation 权限参数、agent 子进程拒绝签名。
- 中文/特殊 Git 路径、tracked symlink、源文件与批次字节上限。
- 真实 Chromium 下 JavaScript POST 默认阻断、`--allow-writes` 放行与终态同源复核。
- CLI 错误边界和路径穿越防护。
- 反构 Precision/Recall、检索 Precision@K/Recall@K/MRR 和真实 Agent 隐藏测试任务。
- SQLite schema upgrade 与 source evidence 字段的旧 digest 兼容。
- schema 升级前备份、失败事务回滚和未来版本拒绝。
- Linux/macOS `fcntl` 与 Windows `msvcrt` 锁后端。
- 所有公开 CLI help 快照、三平台 bundled first-run、零背景研究记录校验。

测试环境约定:

- `LORELOOP_KEY_DIR` 指向临时目录。
- `LORELOOP_TRUST_REGISTRY` 指向临时注册表。
- 测试不会触碰真实 `~/.loreloop/keys/` 或 trust-location 注册表。
- Playwright smoke 测试在依赖缺失时跳过。

---

## 16. 当前状态与后续方向

当前实现已经具备本地最小闭环:

```text
ingest → run → check/verify → report → harvest
```

Codex 采用“一个引擎、原生宿主外壳”的适配方式。`loreloop codex install`
不复制信任逻辑、不直接改写 `config.toml`,而是调用 Codex 自身的 marketplace/plugin
命令注册 `loreloop@loreloop`;GitHub Release 安装器的 `--codex` 选项把 checksummed
LoreLoop 安装和原生插件启用合并为一个流程。Skill 负责会话工作流,核心 CLI 仍负责
证据、验收和人工策展边界。Codex 能执行 shell,因此不增加常驻 MCP 包装层;当前公开
插件校验器不接受 `hooks` manifest 字段,也不复制依赖私有 hook-state 的安装方式。

OpenCode 与 co-mind 遵循同一原则:OpenCode 只安装原生 Skill/command 文件；co-mind 只调用
其 marketplace/plugin CLI。四种宿主共享知识库、证据链和人工授权边界,不会各自复制
信任实现。

仓库内 `eval/` 已经把以下问题做成可重复基准:

- 反构高价值事实的 Precision/Recall 与 forbidden claim 命中。
- context pack 的 Precision@K、Recall@K、MRR 与变长返回精度。
- 有 checked-in 原始结果的四组真实 Agent 编码任务隐藏测试成功率。
- Python/TypeScript/混合仓库反构成本与质量。
- 100/1k/10k 多项目检索、证据链验证与无变更 harvest 延迟。
- 无记忆、会话记忆、代码索引与 LoreLoop 四组任务对照。

`eval/validate_results.py` 从 raw prediction/task/scale 文件重算摘要并执行 CI 阈值；没有
checked-in raw 的历史数字不会进入生成摘要。可用性仍显示 awaiting real participants。

后续可扩展方向:

1. 把固定夹具扩展为更多语言和真实公开仓库,避免把小样本回归分数外推成普遍结论。
2. 为超过 10k 条目的语料引入持久化词法索引,同时保留可解释排序和离线复现。
3. 扩展更多确定性证据适配器,例如 JUnit/SARIF/CI attestation 导入。
4. 更友好的冲突 review 与 supersede 交互。
5. 按已发布协议完成真实零背景参与者测试,持续打磨首次成功路径。
6. 在真实公开仓库上发布可复现案例和失败样本；当前多语言/10k 结果仍是固定或合成夹具。
