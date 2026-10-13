# VG 视频生产与 26 账号矩阵工作台 (Web工作台)
> **AI Agent 上下文导读与开发者架构手册 (AI Coding Guide & Developer Manual)**  
> 任何后续新对话或 AI 助手修改本项目前，**必须先完整阅读本文档**，可在 1 分钟内建立全景认知，避免破坏核心机制。

> 📚 **快速文档导航（按角色查阅）**：
> - 🟢 **业务/运营/新手操作员**：请直接阅读 👉 [《USER_GUIDE.md》（业务极速上手指南）](./USER_GUIDE.md)，零技术门槛，3 分钟学会排产与一键下载无水印成片。
> - 🟡 **不懂代码想改功能/修Bug者**：请直接查阅 👉 [《AGENT_MODIFY_GUIDE.md》（零代码借助 AI Agent 修改指南）](./AGENT_MODIFY_GUIDE.md)，内含拿来即用的 Prompt 模版与防改崩验收清单。
> - 🔵 **开发人员与底层维护者**：请继续向下阅读本文档（系统全景拓扑、核心数据流、API 接口契约与避坑红线）。

---

## 1. 项目定位与系统架构全景

### 1.1 项目定位
本项目是专为 **VG (Video Generation) 视频自动化批量生产流水线** 打造的全栈式排产与调度中枢。它解决了**多账号算力纳管、提示词文件树状检索、多任务拆解排产、Chrome 实例集群唤起、以及豆包 (Doubao) 聊天框零接触全自动注词注图**的全链路闭环。

### 1.2 系统架构拓扑
```mermaid
flowchart TD
    subgraph UI["Web 控制台 (浏览器端)"]
        A["public/index.html (SPA 单页)"]
        A1["26 账号算力池监控网格"]
        A2["目录树任务选择抽屉 (含生成次数微调)"]
        A3["流水线排产看板 (含多选/平滑唤起/切到窗口/卡片平滑进度条)"]
    end

    subgraph Server["本地 Python 服务 (Port 8765)"]
        B["server.py (多线程 HTTP Server)"]
        B1["账号数据与额度原子持久化"]
        B2["VG 资产目录树全层级递归扫描"]
        B3["提示词正文解析 & 参考图智能匹配/Base64直送 (上限10张)"]
        B4["Chrome 独立 Profile 子进程启动 & Windows 原生秒级穿透置顶"]
        B5["流水线状态自动日级快照 (history/)"]
        B6["逐条流式成片下载与扁平入库"]
    end

    subgraph Data["数据持久化层 (data/)"]
        D1["accounts_quota.json (26个账号状态/额度)"]
        D2["daily_tasks.json (当前生产流水线)"]
        D3["history/snapshot_*.json (自动历史快照)"]
    end

    subgraph Chrome["Chrome 独立环境矩阵 (26 Profiles)"]
        C["Chrome 实例集群 (UserData/Profile_xx)"]
        E1["vg_extension (原生 Chrome 扩展 MV3)"]
        E1_SW["service-worker.js (Debugger解密 & 切窗轮询)"]
        E1_CT["content.js (ProseMirror注入 & 15s生命周期防误判)"]
    end

    subgraph Doubao["豆包 Web 端 (https://www.doubao.com/chat/)"]
        F["ProseMirror / Tiptap 富文本输入框"]
        F1["提示词文本精确注入"]
        F2["多张参考图连续 Paste 派发 (最多10张)"]
        F3["HUD 悬浮面板 (直拷/状态/原生下载)"]
    end

    A <-->|REST API JSON| B
    B <-->|读写 JSON| Data
    B -->|命令行参数启动| C
    C -->|加载扩展| E1
    E1 <-->|GET /api/task/context| B
    E1_CT -->|ProseMirror Paste 注入| Doubao
    E1_CT -->|POST /api/task/mark_step| B
    E1_SW -->|Debugger 解密无水印成片| E1_CT
    E1_CT -->|POST /api/task/report_video (严格task_id排他)| B
    B <-->|GET /api/tasks/pending_switch 轮询秒切Tab| E1_SW
```

---

## 2. 核心目录与文件职责清单

```
C:\Users\admin\DeskBox\其他\Web工作台\
├── server.py                   # 后端核心：多线程 HTTP 服务、API路由、Chrome唤起、原生切窗置顶、参考图Base64直送、排他成片入库
├── start.bat                   # Windows 快速启动批处理脚本（双击一键运行 server.py）
├── start.sh / start.command    # macOS 快速启动脚本（双击或终端运行）
├── README.md                   # 本文档（系统架构与开发指南）
├── USER_GUIDE.md               # 业务/运营极速上手指南
├── AGENT_MODIFY_GUIDE.md       # 零代码借助 AI Agent 修改指南（含十大红线与 Prompt 模版）
├── public/                     # 前端静态资源目录
│   └── index.html              # 前端全功能单页（SPA，26账号监控、流水线排产、任务抽屉、流式进度条、吸顶操作栏）
├── vg_extension/               # 原生 Chrome 扩展插件（MV3 架构，官方唯一客户端方案）
│   ├── manifest.json           # MV3 扩展配置，声明 doubao.com 匹配规则与 127.0.0.1 权限
│   ├── content.js              # 内容脚本：HUD 悬浮面板、ProseMirror富文本注入、多图Paste上传、15s生成防误判拦截
│   └── service-worker.js       # 后台服务：Debugger监听无水印AES解密、Tab与会话秒级切窗轮询调度
└── data/                       # 本地数据持久化
    ├── accounts_quota.json     # 26 个 Chrome 资料账号信息（名称、10s/5s/15s剩余额度、状态等）
    ├── daily_tasks.json        # 当前流水线正在执行的任务工单（任务ID、路径、绑定账号、各步骤状态、成片直链）
    └── history/                # 流水线自动快照目录（每次保存或排产自动生成 snapshot_YYYYMMDD_HHMMSS.json）
```

---

## 3. 六大核心数据流与业务实现机制

### 3.1 任务选择与生成次数展开机制 (`repeatCount`)
- **交互入口**：前端「📁 挑选添加任务」抽屉（`task-picker-drawer`）。
- **底层递归扫描**：`server.py` 的 `/api/vg/tree` 通过 `rglob` 扫描全层级目录，不论是一级产品直出脚本还是三级变体迁移目录，均能 100% 完整扫描并生成树状结构。
- **生成次数调整**：
  - 卡片自带步进器 `[-] [ 1 ] [+]`（支持 1~20 次，修改自动勾选卡片）；
  - 顶部工具栏提供「所选各生成 [ N ] 次」批量快捷应用；
  - 底部提交栏实时计算任务总数：`已勾选 M 个提示词，共计 N 条流水线任务`。
- **展开命名规范**：
  - 若提示词生成次数 $= 1$：保持原始场景名（例如 `高效接单`）；
  - 若提示词生成次数 $> 1$：以连续排列展开为多条独立任务，附带自增编号后缀（例如 `高效接单 #1`, `高效接单 #2`, `高效接单 #3`）；
  - 每条展开后的任务分配唯一的全局自增工单号（`V01`, `V02`, `V03`...），独立占有各自的账号分配和生命周期状态。

### 3.2 任务多选隔离与一键平滑唤起机制 (`launchSelectedTasks`)
- **多选边界隔离与防幽灵残留**：
  - 切换分类（如从【全部】切换到【待发送】）时，自动净化过滤不在当前视图可见的任务，防止跨视图隐形勾选泄漏；
  - 【全选当前视图】与【勾选全部待发送】严格采用清空后单向选取逻辑，绝不跨分类累加；
  - 顶部唤起按钮计数 `selected-tasks-count` 严格只统计 `!t.sent`（未发送）任务数量。
- **执行入口终极红线过滤**：
  - `launchSelectedTasks()` 强制过滤出 `tasksToLaunch = currentTasks.filter(t => selectedTaskIds.has(t.id) && !t.sent)`；
  - 若无可唤起任务给出明确提示，100% 杜绝重复唤起已发/已入库任务。
- **按账号智能聚合启动（防丢任务与 Profile 冲突）**：
  - 前端将勾选的所有任务自动按 `assignedAccount` 分组；
  - 同一个账号名下的多条任务，通过 `task_ids: [...]` 单次启动以多标签页形式整齐打开，避免对同一 Profile 进程发起重复锁冲突；
  - 不同账号窗口之间按 **1.5 秒**平滑流式唤起，若总任务数超过 5 条弹窗确认；
  - Windows 调度任务 `VG_Launch_Profile` 设置为 `MultipleInstancesPolicy = Parallel`，批处理移除阻塞死等，确保高频触发零丢单；
  - 唤起完成后，系统自动清空勾选状态。

### 3.3 参考图智能识别、去重、顺序对齐与 Base64 直送机制
> ⚠️ **这是整个系统最精细的核心环节，后续开发切勿随意修改截断规则！**
- **正文提及捕获**：
  - 正则表达式 `re.findall(r"(\w+\.(?:png|jpg|jpeg|webp|PNG|JPG))", prompt_text)` 提取正文提到的所有图片；
  - 严格记录正文中**首次提及的自然顺序**（例如 `6.PNG` $\to$ `5.png` $\to$ `1.png` $\to$ `2.png`）。
- **同名去重与目录优先级**：
  - 扫描产品根目录下的 `产品参考图/`；
  - 若子目录（如 `旧/`）中存在同名文件，**严格以一级根目录文件为准去重**，过滤掉废弃老图。
- **全量 Base64 直送**：
  - **切勿截断为 2 张或 4 张**！凡是正文明确命中的参考图（上限放开至 **10 张**），`server.py` **100% 全量生成完整 Base64 直送数据**；
  - 未命中的主要参考图前 10 张也全量生成 Base64 作为保底；
  - Base64 直送彻底规避了浏览器内容脚本发起跨域网络请求、Mixed Content 拦截或路径包含中文等潜在失败风险。

### 3.4 豆包聊天框无感注词、注图与自动状态回传
- **目标页面与富文本适配**：
  - 豆包网页采用 ProseMirror / Tiptap 富文本框架，普通 `textarea.value = ...` 无法被框架内核感知；
  - 扩展脚本通过派发标准的合成 `ClipboardEvent('paste')`（将正文写入 `DataTransfer` 的 `text/plain`），完美触发 ProseMirror 内部原生处理管道。
- **多张参考图连续 Paste**：
  - 依次将图片的 Base64 转成 `File` 对象，置入 `DataTransfer.items.add(file)` 并派发 `paste` 事件；
  - **每张图片粘贴间隔 450ms**，保证豆包编辑器完成图片上传并生成缩略图，避免并发上传丢图。
- **自动扣减流水线与额度**：
  - 图片与文字全部成功填入后，扩展向本地服务端发起 `POST /api/task/mark_step`；
  - 本地服务自动将流水线该任务的 `sent` 置为 `true`，并根据时长规格（5s/10s/15s）自动扣减对应额度，保存日级历史快照。

### 3.5 豆包成片无水印解密拦截与 15 秒生成生命周期防误判机制
- **后台 DevTools 拦截与无水印解密**：
  - `vg_extension` 依托 Manifest V3 后台服务（`service-worker.js`），通过 Chrome Debugger API 监听豆包接口 `doubao.com/im/chain/single` 的响应数据；
  - 针对豆包返回的密文视频包（`fallback_api`），采用 `QAAB_SALT_HEX` 与 `key_seed` 进行 SHA-512 派生密钥与 AES-CBC 自动解密，无感析出 1080P 纯净无水印视频直链。
- **15 秒生成生命周期防误判拦截**：
  - 豆包 Seedance 视频生成至少需 20~40 秒。当 Chrome 打开已有会话时，豆包会首先拉取历史聊天记录；
  - `content.js` 强制规定：**凡是收到视频距离发词导入时间不足 15 秒的，100% 判定为历史旧视频直接忽略**；
  - 真实发送动作（点击发送或回车）会同步刷新发词时间戳，彻底根治“刚发送一批就显示 V01 就绪”的误判Bug。
- **服务端严格 1 对 1 排他绑定**：
  - `server.py` 的 `/api/task/report_video` 移除了所有模糊账号猜测，严格要求匹配的 `task_id`；
  - 同一视频 URL 实行全局排他防重，绝不串连或重复绑定到其他任务。

### 3.6 卡片级下载进度可视化与逐条流式串行推进 (`download_videos`)
- **卡片内独立百分比进度条与状态流转**：
  - 在每张任务卡片内部内嵌专属进度条控件，流式呈现真实状态：
    `⏳ 正在请求素材...` $\to$ `⏳ 正在拉取视频流...` $\to$ `📥 正在写入磁盘...` $\to$ `✅ 下载完成并入库！`
  - 下载中状态胶囊动态显示 `⏳ 下载中...`，完成瞬间打勾秒变 `💾 素材已入库`。
- **逐条流式串行推进模式**：
  - 点击顶部「📥 一键下载已选」后，采用 `downloadTaskWithProgress` 逐条串行推进下载；
  - 避免并发轰炸 CDN 导致网络失败，下载完成后自动取消勾选、更新顶部入库总进度条，并自动顺畅流转推进至下一条任务。
- **扁平单片归档规范**：
  - 视频文件严格扁平保存在对应产品的：`VG/0XX_产品名/成品/素材/【任务ID_场景标题.mp4】`；
  - 严禁创建子目录或产生 `_v1, _v2` 重复多版本；若本地文件已存在且大小正常，自动秒级标记就绪跳过网络请求。

### 3.7 任务与 Chrome 窗口深度绑定与秒切置顶机制 (`switch_window`)
- 每张任务卡片具备 **「🎯 切到窗口」** 按钮：
  1. 服务端通过 Windows API（`SetForegroundWindow`, `ShowWindow(SW_RESTORE)`）根据任务标题和账号 Profile 精准将对应 Chrome 窗口瞬间置顶到前台；
  2. 扩展 Background ServiceWorker（`service-worker.js`）每 1.2 秒轮询 `/api/tasks/pending_switch`，将目标会话 Tab 瞬间激活为当前活动标签页并高亮提醒，实现任务与浏览器窗口的丝滑无缝直达。

---

## 4. 关键接口契约速查 (API Cheatsheet)

| 方法 | 路径 | 参数 / Body | 说明 |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/system/info` | 无 | 获取当前宿主系统环境（Win/Mac）、VG 实际根路径与 Chrome 路径 |
| `GET` | `/api/accounts` | 无 | 全自动扫描获取所有 Chrome Profile 账号与今日剩余额度（自动自然排序并分配序列号） |
| `POST`| `/api/accounts/update` | `{"id": "Profile 7", "delta_10s": -1, ...}` | 更新账号额度或状态（支持 disabled 禁用与解绑联动） |
| `GET` | `/api/tasks` | 无 | 获取当前流水线工单列表 |
| `POST`| `/api/tasks/save` | `{"tasks": [...]}` | 保存当前流水线工单并生成历史快照 |
| `GET` | `/api/vg/tree` | 无 | 全层级深度扫描 VG 目录结构与提示词树 |
| `GET` | `/api/task/context` | `?task_id=V01` 或 `?account=Profile 7` | 获取指定任务全文、正文顺序参考图（含Base64，上限10张） |
| `POST`| `/api/accounts/launch` | `{"id": "Profile 7"}` | 跨平台打开指定 Chrome Profile 豆包窗口并前台置顶 |
| `POST`| `/api/accounts/launch_task` | `{"account_id": "Profile 7", "task_ids": ["V01"]}` | 启动指定 Chrome Profile 并打开带任务参数的豆包（Win/Mac自适应） |
| `POST`| `/api/tasks/switch_window` | `{"task_id": "V01", "account_id": "Profile 7"}` | 瞬间激活并将该任务所在的 Chrome 窗口置顶到最前台 |
| `GET` | `/api/tasks/pending_switch`| 无 | 供扩展 Background 轮询待激活的任务与会话并秒切 Tab |
| `POST`| `/api/tasks/pending_switch/ack` | `{"task_id": "V01"}` | 扩展回执切窗动作完成 |
| `POST`| `/api/task/bind_session` | `{"task_id": "V01", "session_url": "..."}` | 回传任务与豆包会话的具体 URL，支持断点切窗 |
| `POST`| `/api/task/mark_step` | `{"task_id": "V01", "step": "sent", "value": true}` | 标记任务流转步骤并联动扣减账号额度 |
| `POST`| `/api/task/report_video`| `{"task_id": "V01", "video_url": "..."}` | 扩展上报捕获到的无水印视频直链（严格 1 对 1 排他校验） |
| `POST`| `/api/tasks/download_videos` | `{"task_ids": ["V01"]}` | 服务端流式下载视频到对应产品的【成品/素材/】目录 |
| `GET` | `/api/video` | `?path=...` | 本地成片流式预览接口 |
| `POST`| `/api/vg/open_folder` | `{"type": "vg_root" \| "extension" \| "product_ref"}` | 跨平台在系统文件管理器 (资源管理器/Finder) 中打开指定目录 |
| `GET` | `/api/tasks/snapshots` | 无 | 获取历史自动保存的时间戳快照列表 |
| `POST`| `/api/tasks/restore_snapshot` | `{"filename": "snapshot_..."}` | 一键回滚恢复历史快照数据 |

---

## 5. 本地开发、调试与运维常用命令

### 5.1 启动 / 重启后台服务

- **Windows 用户**：
  双击根目录下的 `start.bat`，或在命令行中运行：
  ```powershell
  python server.py
  ```
- **macOS 用户**：
  - **双击启动**：直接双击根目录下的 `start.command`（若首次提示权限，终端执行一次 `chmod +x start.command` 即可）；
  - **终端启动**：在终端中运行：
    ```bash
    chmod +x start.sh
    ./start.sh
    ```
  *(脚本将自动识别 python3 环境并唤起浏览器访问 `http://127.0.0.1:8765/`)*

### 5.2 检验接口与参考图 Base64 状态
当怀疑参考图丢图或接口未返回 Base64 时，可使用一行 Python 快速自测：
```powershell
python -c "import urllib.request, json; res = json.loads(urllib.request.urlopen('http://127.0.0.1:8765/api/task/context?task_id=V01').read().decode()); [print(f'{img[\"name\"]} | matched: {img[\"matched\"]} | b64: {len(img.get(\"base64\", \"\")) > 100}') for img in res.get('data', {}).get('ref_images', [])]"
```

### 5.3 Chrome 原生插件更新与调试
- 原生插件源码目录：`C:\Users\admin\DeskBox\其他\Web工作台\vg_extension\`；
- 安装/调试入口：在 Chrome 中打开 `chrome://extensions`，开启「开发者模式」，点击「加载已解压的扩展程序」，选择上述目录；
- 若修改了 `content.js` 或 `service-worker.js`，只需在扩展管理页面点击该扩展卡片上的 **刷新 🔄 图标**，并在已打开的豆包网页中按 F5 即可立即生效。

---

## 6. 后续修改高危避坑准则 (Agent Critical Rules)

> ⚠️ **AI 助手在后续对话中改动本工程代码时，必须严格遵守以下十大红线：**

1. **严禁硬编码图片截断限制**：
   - 历史教训：代码中曾因出现 `matchedImgs.slice(0, 2)` 和 `if len(ref_images) < 4`，导致 4 张参考图被强行截断丢失后两张；
   - 现行标准：正文命中的参考图及 Base64 直送上限全链路放宽至 **10 张**，绝不可在 `server.py` 或 `content.js` 中重新引入写死的小阈值截断！
2. **统一使用原生扩展方案（弃用油猴脚本）**：
   - 本工程已全量收敛至原生 Chrome 扩展（`vg_extension/`，MV3 架构）；
   - 油猴脚本已全面弃用并移除，所有自动化填词、多图直贴、视频捕获、窗口激活与下载统一维护在 `vg_extension` 下。
3. **成片就绪严格防误判（15 秒生成铁律与任务 ID 排他绑定）**：
   - 豆包 Seedance 视频生成至少需 20~40 秒。在 `content.js` 中严禁接收发词后 **15 秒内** 的视频（必为页面载入的历史旧聊天残留）；
   - `server.py` 的 `/api/task/report_video` 必须强制校验明确的 `task_id`，严格禁止按账号做模糊猜测错绑，且视频直链实行全局排他防重。
4. **逐条流式串行推进与卡片独立下载进度可视化**：
   - 批量下载必须采用单任务串行流式推进（`downloadTaskWithProgress`），每张卡片必须在卡片内展示独立的动态百分比进度条（0%~100%），下载完成瞬间打勾转为【💾 素材已入库】并顺畅推进下一条，严禁并发打崩 CDN 或缺少进度反馈。
5. **多选操作边界隔离（所见即所选，防幽灵勾选残留）**：
   - 切换分类视图（如切到【待发送】）时，必须净化过滤掉不在当前视图可见的任务勾选，严禁隐藏选择泄漏；
   - 唤起执行入口 `launchSelectedTasks` 必须严格过滤 `!t.sent`（仅唤起未发送任务），且按钮数字精准匹配待发条数，严禁重复拉起已发/已入库任务。
6. **任务与 Chrome 窗口深度绑定（一键秒切）**：
   - 每张任务卡片必须保留【🎯 切到窗口】功能，支持通过 Windows 原生置顶与 Background ServiceWorker 双重穿透将任务所在窗口及具体会话瞬间置顶。
7. **保持服务与数据端口一致 (8765)**：
   - 扩展所有 API 请求硬编码指向 `http://127.0.0.1:8765`，切勿随意更改端口号。
8. **保存数据必须保留日级快照**：
   - 所有变更 `daily_tasks.json` 的保存接口必须保留并调用 `save_daily_tasks(..., make_snapshot=True)`，这是用户回滚防误操作的生命线。
9. **遵循 VG 资产目录规范**：
   - 凡涉及在 `C:\Users\admin\DeskBox\其他\VG` 下新建或检索资产，严格遵循 `0XX_产品名/` $\to$ `脚本名/` $\to$ `迁移_xxx/` 三级树结构。
10. **时长规格动态算力换算与账号禁用联动**：
    - 系统根据提示词正文提取的时长规格（5s: 10条, 10s: 5条, 15s: 4条）按基准分值进行动态均衡调度与扣减；
    - 处于禁用状态（`status: 'disabled'`）的账号，必须在智能调度候选池和手动分配下拉菜单中 100% 过滤排除；在禁用时必须联动自动解绑当前流水线中分配给该账号且未发送的任务。


