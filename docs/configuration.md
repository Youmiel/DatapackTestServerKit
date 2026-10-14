# 配置详解

工具默认读取项目根目录的 `config.yaml`（可从 `config.example.yaml` 复制）。
`--config <path>` 可指定其它文件。以下为各配置块说明。

## 顶层结构

```yaml
java:             # Java 运行时探测
network:          # 网络下载（超时/代理/内容校验/重试）
modrinth:         # Modrinth API（匿名可用）
curseforge:       # CurseForge API（需 key）
mcdr:             # MCDR 全局设置
server:           # 端口 / RCON / 启动命令（全局默认，组级可覆盖）
plugins:          # MCDR 插件全局默认（组级可覆盖/追加/禁用）
version_groups:   # 服务器版本组（核心）
```

---

## java — Java 运行时探测

```yaml
java:
  launcher_roots: ["C:/Program Files (x86)/Minecraft Launcher/runtime"]
  use_env_fallback: true
```

- `launcher_roots`：MC 官方启动器的 `runtime` 目录，可多个、**可留空**。
  工具只使用官方 `java-runtime-*` 运行时（忽略其它 jdk/jre）。常见位置：
  - Windows：`C:/Program Files (x86)/Minecraft Launcher/runtime` 或 `%APPDATA%/.minecraft/runtime`
  - Linux/macOS：`~/.minecraft/runtime`
- `use_env_fallback`：默认 `true`。为 `true` 时若 launcher_roots 找不到，回退 `JAVA_HOME` / `PATH`。

### 所需 Java 自动推断（无需人工映射表）

部署时工具查询 **Mojang 官方 version manifest**，得到该 MC 版本官方要求的
Java（大版本 + 运行时组件名）。例如：1.20.1→Java17/`java-runtime-gamma`，1.21.1→Java21/`java-runtime-delta`。
然后：
1. 在 `launcher_roots` 里**优先精确定位同名官方运行时**（最准）；
2. 否则按大版本匹配已装 Java（JAVA_HOME / PATH）；
3. 匹配不到**明确报错**列出可用 Java（不默默用错版本）。

结果缓存到 `.cache/`（24h）。若某版本组显式配了 `preferred_major`，则以配置为准（不查 Mojang）。

---

## network — 网络下载 / 失败重试

```yaml
network:
  timeout: 60             # 单次请求/下载超时（秒）
  proxy: ""               # 可选代理；工具默认不读系统代理
  verify_sha1: true       # 是否做内容校验（见下）
  download_retries: 3     # 每个请求/下载失败的总体重试次数（含首次）
```

- `timeout`：单次请求/下载超时（秒），默认 `60`。
- `proxy`：可选代理（如 `http://127.0.0.1:7890`）。工具**不读**系统/环境代理，
  只有这里显式填了才走代理。
- `verify_sha1`：是否做**内容校验**，默认 `true`。开启时：
  - **mod**：用 Modrinth/CurseForge 给出的 sha1 校验下载结果；复用共享缓存或实例
    已有文件时**本地重算 sha1 比对**（不联网）；
  - **MCDR 插件**：用 HTTP `HEAD` 取 `ETag`/`Last-Modified`/`Content-Length` 与
    缓存记录比对，判断共享缓存是否失效（不下载内容）；探测不到时沿用缓存。

  关闭后退化为“文件存在且非空即复用”（不校验内容）。
- `download_retries`：每个请求/下载失败后的**总体尝试次数**（含首次），默认 `3`
  （首次失败后最多再重试 2 次，间隔退避）。连接层对 5xx/429 已先自动重试，
  此项兜底**断流 / 超时 / SHA1 校验失败**等整体错误。设为 `1` 即关闭总体重试。

---

## server — 端口 / 游戏模式 / RCON / 启动命令

```yaml
server:
  port: 25565              # 游戏端口（server-port）
  gamemode: creative       # 默认游戏模式（survival/creative/adventure/spectator 或 0-3）
  rcon:
    enable: false          # 是否启用 RCON
    port: 25575            # RCON 端口
    password: ""           # RCON 密码（启用时建议设置；留空默认 "password"）
    # address: 127.0.0.1   # MCDR 连接用，一般不用改
  start_command_template: ""   # 自定义启动命令（可选）
```

### 游戏模式 gamemode

写 `server.properties` 的 `gamemode`。MC 默认是 `survival`（生存），但本工具用于
**测试数据包**，因此**未配置时默认 `creative`**（创造，方便搭环境/验证效果）。

- 顶层 `server.gamemode` 配全局默认；组级 `server:` 块可覆盖（只影响该组）。
- 合法值：`survival` / `creative` / `adventure` / `spectator`（也接受数字 0-3）。
- 部署后冷改：`mcserver configure-server --version X --gamemode survival`（见 usage；旧名 set-ports 也可用）。

### 端口 / RCON

- `deploy` 时自动应用（写 `server.properties` + 同步 MCDR `config.yml` 的 rcon 段）。
- RCON 需**两侧一致**（MC 服务端与 MCDR 客户端），工具自动保持同步。
- 部署后可用 `mcserver configure-server` 冷修改（见 usage），改后重启服务端生效。

### 自定义启动命令（内存 / JVM 参数）

默认启动命令为 `java -Xms1G -Xmx2G ... -jar minecraft_server.jar nogui`（1G 内存）。
自定义请给 `start_command_template`（shell 命令串）：

```yaml
server:
  start_command_template: '"{java}" -Xms2G -Xmx4G -XX:+UseG1GC -Dfoo=bar -jar {jar} nogui'
```

占位符：
- `{java}` → 工具探测到的 java 绝对路径。**路径含空格时请加引号**：`"{java}"`
- `{jar}` → 服务器 jar 文件名（`minecraft_server.jar`）

**不配模板时保持默认**（exec 直启，不经 shell）；配了则按模板（shell 方式）生成。
改动后需重新 `deploy`（或手动改实例 config.yml）生效。

---

## plugins — MCDR 插件（全局默认 + 组级覆盖）

```yaml
plugins:                       # 顶层 = 全局默认，所有组共用
  - id: quick_backup_multi
    url: "https://github.com/TISUnion/QuickBackupM/releases/download/v1.10.1/QuickBackupM-v1.10.1.mcdr"
```

- `id`：插件标识（仅展示/记录）。
- `url`：插件文件直链（GitHub release asset 等），工具下载到实例 `plugins/`，
  MCDR 启动时自动加载。插件目录 <https://mcdreforged.com/en/plugins> 可查 release 直链。

顶层 `plugins` 是**全局默认**（通用插件只写一次）；组级 `plugins` 与之合并：

| 组级写法 | 效果 |
| --- | --- |
| `{id: X, url: ...}` | 覆盖同名全局插件（换源 / 换版本） |
| `{id: X}` | 覆盖但沿用全局 url（少写） |
| `{id: X, disabled: true}` | 从该组结果中**排除**该全局插件 |
| 新的 `id` + `url` | **追加**为该组独有的插件 |

合并后顺序：全局插件在前（按声明顺序），组级新增在后。

**插件跨 MC 版本通用，带共享缓存与失效判定**：插件文件不区分 MC 版本，工具统一先
落到 `.cache/plugins/`（同一 URL 只下载一次），再复制到各实例 `plugins/`。是否失效
**不需要下载即可判定**：对比缓存索引里记录的 HTTP 指纹（部署时的响应头）与一次
`HEAD` 探测的结果 —— 指纹变了、或 URL 改了才重新下载。探测不到（无网、服务端不
支持 HEAD）时**沿用缓存并提示**，不阻塞部署。`deploy --force` 强制重下并刷新指纹。

实例侧同样按内容比对（本地重算 sha1），实例文件被改坏会自动用缓存覆盖；
`network.verify_sha1: false` 时退化为“存在即复用”。

---

## version_groups — 服务器版本组（核心）

```yaml
version_groups:
  - id: "1.21"                        # 组标识
    instances: ["1.21.1", "1.21.4"]   # 要部署的小版本（增删即调数量）
    # java: { preferred_major: 21 }   # 可选：强制 Java 大版本（一般不填，自动推断）
    # server: { ... }                 # 可选：覆盖顶层 server 设置
    mods: []
    plugins: []
```

每个组 = 一个大版本线，组内共享 mod 列表与 Java 要求（通用插件见顶层 `plugins`）。
组下每个小版本生成一个独立服务器（`servers/<版本>/`，均为自包含 MCDR 根）。

### mod — fabric mod（Modrinth / CurseForge）

```yaml
mods:
  - id: carpet            # Modrinth slug（默认 source）
    source: modrinth
  - id: fabric-api
    source: modrinth
  # - id: some-cf-mod     # CurseForge 项目名
  #   source: curseforge
```

- `source: modrinth`（默认）：**无需 key**，自动选该 MC 版本 + fabric 的最新稳定版。
  可选 `version` 填 version_id 锁定。
- `source: curseforge`：需 API key（不写进配置，用环境变量提供，见下）。

### plugins — 组级 MCDR 插件差异

通常不需写：通用插件放顶层 `plugins`（见上文 “plugins — MCDR 插件” 一节），
这里只写与全局不同的部分（覆盖 / 排除 / 追加）。

---

## modrinth / curseforge

```yaml
modrinth:
  user_agent: "your-name/tool (https://github.com/you)"   # 建议改成你的标识
curseforge:
  api_key_env: "CURSEFORGE_API_KEY"   # 从该环境变量读 key
```

- Modrinth 匿名可用，但官方要求有辨识度的 User-Agent。
- CurseForge 需要 API key，通过环境变量提供，不落盘：
  ```powershell
  $env:CURSEFORGE_API_KEY = "你的 key"
  ```

---

## mcdr

```yaml
mcdr:
  pip_spec: "mcdreforged>=2.0"   # MCDR pip 版本约束
  language: "zh_cn"              # en_us / zh_cn / zh_tw
```

MCDR 通过 pip 装进项目共享 venv（一次即可），每个实例是独立运行目录。

---

## 下载缓存与资源复用

所有跳实例复用的东西都放在项目根的 `.cache/`：

| 目录 | 内容 | 复用方式 | 失效判定（不下载） |
| --- | --- | --- | --- |
| `.cache/mods/` | fabric mod | 按文件名共享，实例复制 | API 给出的 sha1（本地重算比对） |
| `.cache/plugins/` | MCDR 插件 | 按文件名共享，实例复制 | HTTP 指纹（ETag/Last-Modified/Content-Length） |
| `.cache/libraries/` | fabric 依赖库 | 实例 `server/libraries` **目录链接**至此 | Maven 按版本目录共存，无需失效 |
| `.cache/mojang_java/` | 各 MC 版本 Java 要求 | 全局共用 | 24h TTL，网络失败降级旧缓存 |

`.cache/mods/`、`.cache/plugins/` 下各有一个 `_index.json` 记录来源 URL、sha1、
HTTP 指纹；实例根有 `.deploy-meta.json` 记录该实例部署时用的 fabric loader/
installer 版本、各 mod 的 sha1、各插件指纹。

### `deploy` 幂等/补齐的判定依据

| 资源 | 判定依据 |
| --- | --- |
| mod | 本地重算 sha1 vs Modrinth/CurseForge 给出的 sha1 |
| MCDR 插件 | HEAD 指纹 vs 缓存索引；URL 变化也算失效 |
| fabric server jar | 元数据记录的 loader/installer 版本 vs 当前解析结果 |
| Java 要求 | 24h TTL |

> **注意**：判定“失效”只覆盖**同名文件**。同一 mod 的不同文件名旧版本
> （如 `fabric-api-0.145.4.jar` 与 `fabric-api-0.155.2.jar`）不会被自动清理，
> 需手动删除 —— 否则 Fabric 会因重复 mod 报错。

### libraries 共享（目录链接）

实例的 `server/libraries` 默认做成指向 `.cache/libraries/` 的目录链接
（Windows `junction`，Linux/macOS `symlink`）。各 MC 版本的依赖库按 Maven 布局
叠加共存，互不冲突。实测 20 个多版本实例合计约 790MB 的库可压到约 243MB。

`deploy` 遇到旧实例的真实 `libraries/` 目录时，会**自动把内容移动合并**进共享
缓存并替换为链接（`mcserver status` 会显示 `libraries: 已共享 / 独立`）。

为什么只能用链接：fabric 的 server jar 实为 installer bootstrap，它只检查
`.fabric/server/fabric-loader-*.jar` 列出的一些文件**是否存在**，且库目录路径在
其中硬编码、不可配置 —— 因此无法用“下载到缓存再复制”的方式共享。
附带收益：fabric 若判定安装不完整而重装，写入会经链接落到共享目录。

> ⚠️ 删除整个实例目录时请注意：Windows 的 `shutil.rmtree` 会**跟随 junction**
> 删掉共享库内容（`libraries.remove_link()` 提供只断链接的安全做法）。
