# LoreLoop

[English](README.md) | [简体中文](README.zh-CN.md)

**反转项目知识，指导编码代理，让验收结果回流为可信知识。**

LoreLoop 是面向编码代理的本地知识治理与证据验收工具。它不会替代 Codex、Claude
Code、OpenCode 或 co-mind；用户继续留在原来的编码代理会话中，LoreLoop 在背后完成：

1. 从代码和运行中的 Web 应用反向提取项目知识；
2. 按可信度检索并注入与当前需求相关的知识；
3. 用可复审证据验收结果；
4. 只在操作者明确授权后，把已验收结果回流为知识。

完整的产品设计、安全模型、评估数据和开发文档请参阅英文
[README](README.md)。下面提供完整的中文安装与首次使用说明。

## 安装要求

- 当前机器已安装 Codex、Claude Code、OpenCode 或 co-mind 中的至少一个；
- Linux/macOS 使用 `curl` 或 `wget`；
- 安装过程需要 `uv`、`pipx` 或 Python 3.11–3.14；
- 浏览器探索与验收是可选能力，需要 Playwright。

## 让当前编码代理安装

把下面这段直接发给 Codex、Claude Code、OpenCode 或 co-mind：

```text
请为正在运行本次对话的编码代理安装并配置 LoreLoop。

请完整阅读 README 的“安装”部分并严格执行，不要只做总结：
https://raw.githubusercontent.com/zhangguiping-xydt/loreloop/main/README.zh-CN.md

请识别当前宿主，使用对应的安装选项完成 LoreLoop 安装，然后运行
loreloop doctor 和当前宿主对应的状态检查，并用中文汇报结果。

不要要求我单独安装或理解其他执行组件；不要直接修改 .loreloop、宿主配置文件
或 marketplace 文件；安装过程中不要执行 trust reset、complete、harvest 或知识策展。
```

用户只需要表达“安装 LoreLoop”。安装过程中使用的 Python 包、本地命令和宿主插件
属于内部实现，不需要作为独立产品步骤解释给用户。

## 推荐：GitHub Release 一次安装

安装器会安装 LoreLoop、连接指定宿主，并验证下载包的 `SHA256SUMS`。四种宿主选项
可以组合；由 Agent 安装时只选择当前对话所在的宿主。

Linux/macOS：

```bash
curl -fLO https://github.com/zhangguiping-xydt/loreloop/releases/latest/download/install-loreloop.sh

sh install-loreloop.sh --codex
sh install-loreloop.sh --claude
sh install-loreloop.sh --opencode
sh install-loreloop.sh --comind
```

Windows PowerShell：

```powershell
Invoke-WebRequest https://github.com/zhangguiping-xydt/loreloop/releases/latest/download/install-loreloop.ps1 -OutFile install-loreloop.ps1

.\install-loreloop.ps1 -Codex
.\install-loreloop.ps1 -Claude
.\install-loreloop.ps1 -OpenCode
.\install-loreloop.ps1 -CoMind
```

需要浏览器探索或浏览器验收时，在 Linux/macOS 增加 `--with-web`，在 Windows 增加
`-WithWeb`。不要把远端脚本直接通过管道交给 shell，也不要绕过校验失败。

## 首个 Release 发布前：从 GitHub 安装

如果仓库还没有 GitHub Release，下载地址返回 404，可以从默认分支安装：

```bash
uv tool install --force \
  'loreloop[web] @ git+https://github.com/zhangguiping-xydt/loreloop.git@main'
```

不需要浏览器能力时移除 `[web]`。然后只执行当前宿主对应的一条命令：

```bash
loreloop codex install --source zhangguiping-xydt/loreloop --ref main
loreloop claude install --source zhangguiping-xydt/loreloop
loreloop opencode install
loreloop comind install --source zhangguiping-xydt/loreloop
```

只有“尚无 Release”或操作者明确要求预发布版本时才能使用源码安装。正式 Release
存在但校验失败时必须停止，不能切换到可变分支绕过校验。

## 安装后验证

先运行：

```bash
loreloop doctor
```

再运行当前宿主对应的一条状态命令：

```bash
loreloop codex status
loreloop claude status
loreloop opencode status
loreloop comind status
```

插件安装完成后需要新开宿主会话，让宿主重新发现 Skill。

## 在项目中首次使用

如果用户明确要求在当前项目使用 LoreLoop，运行：

```bash
loreloop init --skill
```

之后可以在当前编码代理中直接说：

```text
使用 LoreLoop 基于这个老项目开发一个新功能。
```

宿主会运行 `loreloop begin`，读取相关项目知识，并继续在当前会话完成开发。

OpenCode 也可以使用：

```text
/loreloop 基于这个项目开发新功能
```

Codex 还可以显式调用 `$loreloop`。

## 安全边界

- 不要直接编辑 `.loreloop`；
- 不要手工改写宿主配置或 marketplace 文件；
- 安装过程不得执行 `trust reset`；
- 未经操作者针对具体 run 明确确认，不得执行 `complete --confirm`；
- `harvest`、approve、reject、supersede 等知识回流与策展操作始终需要明确授权；
- OpenCode 当前没有可验证的工作区沙箱，因此不支持
  `loreloop run --agent opencode`，但支持当前 OpenCode 会话内的交互式使用。

## 卸载宿主集成

```bash
loreloop codex uninstall --remove-marketplace
loreloop claude uninstall --remove-marketplace
loreloop opencode uninstall
loreloop comind uninstall --remove-marketplace
```

OpenCode 卸载只会删除内容仍与 LoreLoop 模板完全一致的文件；用户修改过的文件会保留。
