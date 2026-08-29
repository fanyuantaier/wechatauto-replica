[**English**](README.md) | [**中文**](README.zh-CN.md)

# wechatauto —— 微信 4.x Windows 客户端自动化（wxauto 复刻版）

![PyPI version](https://img.shields.io/pypi/v/wechatauto-replica)
![PyPI downloads](https://img.shields.io/pypi/dw/wechatauto-replica)
![Python](https://img.shields.io/pypi/pyversions/wechatauto-replica)
![License](https://img.shields.io/github/license/fanyuantaier/wechatauto-replica)
![GitHub stars](https://img.shields.io/github/stars/fanyuantaier/wechatauto-replica)


本项目复刻上游 wxauto 项目，目标是实现对当前微信 4.x Windows 客户端的自动化
（读取消息、发送消息、媒体下载、朋友圈），非网页版，直接操作本机客户端。

> 当前版本：1.1.10
>
> **兼容范围**：Windows 10/11 ｜ Python 3.9+（已在 3.12 验证）｜ 微信 **4.1.12+**
> （数据库读取路线对微信版本不敏感；坐标+OCR 发送路线依赖 4.1.12+ 自绘渲染
> 布局，其它 4.x 小版本可能需校准 `guia.py` 布局常量）。

![解密读取微信 4.x 加密数据库](docs/demo_db_files.gif)

*直接解密读取 `xwechat_files/.../db_storage/` 下的 `contact.db` / `message_*.db` / `sns.db` 加密库——纯本地，无 Web API。*

---

## 🤝 致谢

> 感谢 [vesio](https://github.com/vesio) 在 [issue #1](https://github.com/fanyuantaier/wechatauto-replica/issues/1) 提供微信 4.1.12 的 UIA 控件树代码与思路，促成了 v1.0.8 的 UIA 混合驱动。
>
> 感谢 [nanshanjack](https://github.com/nanshanjack) 发现 UI 锁的可重入问题（v1.1.2 修复）。
>
> 感谢 [maozhitao12450](https://github.com/maozhitao12450) 报告 WXAM (wxgf) 图片下载问题（v1.1.3 修复）。
>
> 感谢 [uiharukazari0105](https://github.com/uiharukazari0105) 发现语音数据分片存储（`media_1.db` 等）从未被搜索的问题（v1.1.4 修复）。

---

## 版本记录

### v1.1.10（2026-08-27）

- **新增原图下载功能**：`MediaDownloader.download_image_original()` 通过UI自动化点击图片消息，触发微信下载原图。解决了群聊图片只有缩略图可用的限制。
- **修复长文本消息内容提取**：添加zstd解压支持、`compress_content`回退，修复换行符处理问题。

### v1.1.9（2026-08-27）

- **修复微信4.1.13+密钥提取**：调整密钥提取优先级，将`Config.Cipher`内存扫描置于`extract_master_key_from_cfg`之前。cfg提取方式在微信4.1.13.12上返回错误的主密钥，而Config.Cipher扫描（从XOR解码的blob中读取原始`enc_key`值）工作正常。此修复解决了新版微信"0/24密钥验证通过"的问题。

### v1.1.8（2026-08-25）

- **修复 MediaDownloader 缺失 `_derive_xor_key` 方法**：v1.1.7 发布时意外遗漏了 `_derive_xor_key()` 方法，但代码路径（`_decrypt_v2`、`detect_image_key`）仍引用它，导致图片解密时出现 `AttributeError`。已恢复该方法，用于从缩略图 `_t.dat` / `_h.dat` 文件反推 XOR 密钥。

### v1.1.6.1（2026-08-20）

- **PyPI 描述修复**：1.1.6 发布时漏同步 `README_pypi.md`（描述停留在 1.1.5.1），本补丁版补全 v1.1.6 更新记录并同步版本号。

### v1.1.6（2026-08-20）

- **缺密钥报错自动诊断**：`数据库无可用密钥` 报错前会自动检测三项最常见根因——Python 位数（32 位读不了 64 位微信内存）、逐个微信进程的 `OpenProcess`/`ReadProcessMemory` 读取权限、多账号目录与所选账号对比（提示用 `WeChatDB(account=...)` 显式指定），无需先手动运行 `diagnose_keys`。
- **新增诊断工具**：`wechatauto/diagnose_keys.py`（微信登录后运行 `python -m wechatauto.diagnose_keys`）输出库版本、Python 位数、微信进程 PID 及逐个进程的读取权限检测、磁盘全部账号与所选账号对比、已缓存密钥、进程内存重新提取结果与密钥校验情况——报密钥提取问题时把输出完整发给维护者即可定位。
- **跳过 `migrate\unspportmsg.db`**：该库是微信保留的「未支持消息」库，进程内存中无对应密钥、代码也从不会访问；此前它会让每次初始化都触发一次全进程内存扫描。

### v1.1.5.1（2026-08-18）— 测试版 / beta

- **修复实时监听不触发**：`WeChatDB.get_new_messages()` 引用了未定义的 `found`（NameError 被 `Listener._poll_once` 吞掉），导致消息回调从未触发——包括从未聊过天的联系人的首条消息。
- **动态消息分片**：`_message_dbs()` 现在会重新扫描磁盘，微信运行中新建的分片（如 `message_5.db`）会被自动发现并提取密钥。

### v1.1.5（2026-08-18）

- **版本号规范化**：语音跨库下载修复后整理补丁版本号（1.1.4.2 → 1.1.5）。

### v1.1.4.2（2026-08-18）

- **PyPI 描述清理**：移除 v1.1.4 版本记录中关于 demo 默认群改动的条目。

### v1.1.4.1（2026-08-18）

- **PyPI 页面中英双语**：PyPI 描述合并中文（`README.zh-CN.md`）与英文（`README.md`）两个版本，中文版在包页面可见。

### v1.1.4（2026-08-18）

- **跨全部媒体库下载语音**：`download_voice()` 现在搜索所有 `media_*.db`（不再只查 `media_0.db`）——微信把语音分片存到多个媒体库；此前存在 `media_1.db` 等的语音无法找到（感谢 uiharukazari0105）。
- **群聊图片缩略图回退**：群聊的图片原图只有被点开（查看）后才会落盘本地；原图未点开不下发时，`download_image` 自动回退到缩略图（`_t.dat`），保存为带 `_thumb` 后缀的文件。
- **`WeChatDB._find_media_rows(user, types)`**：新增批量查媒体接口——返回某会话指定 `local_type` 集合的全部媒体 `local_id`（用于批量下载）。
- **`demo_media.py --images N`**：按 `local_type` 直接从数据库下载某会话最近 N 张图片，绕过总消息数 `--limit` 的限制——群聊消息上万条时不再「只列出几张图」。

### v1.1.3（2026-08-17）

- **WXAM (wxgf) 图片解码**：微信 4.x 现在把**普通图片**（不仅是动图贴纸）也存进 WXAM 容器
  （内部为 HEVC 比特流）。`MediaDownloader.download_image` 新增 wxgf 处理：提取 HEVC
  Annex-B 流，用 ffmpeg 转码为 JPG（优先用 `imageio-ffmpeg` 内置二进制，其次 PATH 上的
  ffmpeg）；ffmpeg 不可用时不再丢弃数据，改为保存原始解密数据为 `.wxgf` 兜底。
- 新增依赖：`imageio-ffmpeg>=0.4.9`。

### v1.1.2（2026-08-16）

- **UIA 驱动线程安全**：`WeChatUIA` 实例化时在当前线程初始化 COM（`CoInitializeEx`，幂等）——修复后台线程/宿主进程（如 WeChatBot）实例化报「尚未调用 CoInitialize / 无法加载 UIAutomationCore.dll」。
- **主窗口过滤**：只认加载了 `Weixin.dll` 的主进程窗口，过滤无 DLL 的辅助进程窗口（其热激活必然失败，不再刷噪音警告）。
- **转发语音修复**：`Chat.ForwardVoiceMessage` 未指定目标时用 `self`（原 `_cur()` 可能误取会话）。
- **UI 锁可重入**：`LockManager` 同线程可重入——`@uilock` 函数互相调用（如 `ForwardVoiceMessage` → `VoiceMessage.forward_to`）不再死锁。

### v1.1.1（2026-08-16）

- **撤回消息**（`Chat.RecallLastMessage` / `uia_driver.recall_last_message`）：右键最新一条自己发的消息 → UIA 优先
  （主窗口树内 `mmui::XMenuView` 菜单项定位「撤回」，Invoke/Select 或鼠标点击），OCR 兜底（全屏识别「撤回」
  文字定位点击）；菜单只剩「删除」（超过 2 分钟撤回时限）时返回失败。
- **UIA 健壮性**：菜单项查找限定在主窗口子树内（避免触发 Windows UIA 根遍历的系统挂起 bug）；移除脆弱的
  `WindowControl(ClassName=...)` 兜底定位。
- **媒体修复**：视频 id bytes→str 解码（`MediaDownloader`），修复视频文件定位。
- `demo_media.py` `--photos` 默认 3 → 10。

### v1.1.0（2026-08-15）

- **图片 AES 密钥自动监控捕获**（`media.py`）：微信 4.x 的 V2 图片 AES 密钥仅在
  查看图片大图时短暂驻留进程内存（实测约 5 分钟后释放）。`_scan_aes_key()` 新增
  `monitor` 模式——首次扫描未命中时自动持续轮询并提示去微信点开一张图片看大图，
  密钥进入内存后自动捕获并持久化到 `image_keys.json`，之后免扫描直接解密。
  首次用户无需手工找密钥，看图一次即可完成配置。
- **修复进程排序扫描 bug**：移除 `_scan_aes_key` 中按内存占用排序进程的逻辑
  （`GetProcessMemoryInfo` 结构体大小传错导致工作集全为 0，`reverse` 排序反而把
  主进程排到最后，错过密钥驻留窗口），恢复按微信进程原顺序扫描（主进程优先命中）。
- **语音/视频/文件不受影响**：仅图片 `.dat` 为 V2 AES 加密需密钥；语音（SILK）、
  视频（MP4）、文件均为明文直接读取。

### v1.0.9（2026-08-14）

- **open_chat 账号/微信号搜索修复**（`uia_driver.py`）：微信搜索框不认 wxid
  （系统账号），`open_chat` 传入 username 时自动通过本地 DB 映射为昵称/备注/
- **感谢 [uiharukazari0105](https://github.com/uiharukazari0105)** 报告 v1.1.7 版本缺失 _derive_xor_key 方法的 bug。
  微信号再搜索（`_resolve_search_keyword`），并清空搜索框残留重试；
  实测 `open_chat('wxid_sb9or2x9zxj012')` 成功。
- **UIA 表情包精确读取**（`msgs/mtype.py` + `uia_driver.py`）：热激活后消息
  列表暴露 `mmui::RecyclerListView`，新增 `find_in_message_list()` 用鼠标滚轮
  驱动虚拟化列表滚动，按 ClassName/Name 定位表情行并取 BoundingRectangle
  精确坐标；`EmojiMessage.capture()` 优先走 UIA 定位 + 方向感知气泡裁剪
  （`_crop_bubble_from_row`），实测 1.1s 裁出 271×271 表情，替代原先
  「截图全消息区 + 连通域猜气泡」的脆弱方案；失败自动回退原连通域逻辑。
- **语音通话**（`uia_driver.voice_call` + `Chat.VoiceCall`）：标题栏暴露
  `mmui::ChatVoIPView.voip_button`（Name=语音通话），控件树动态重建需重试
  定位；video=True 尝试找视频通话按钮（当前版本未暴露，通常失败）。
- **拍一拍**（`uia_driver.poke` + `Chat.Poke`）：微信 4.x 拍一拍只能通过
  右键对方头像触发，菜单为自绘不暴露 UIA；实现为「内容重心定位 friend
  消息行 → 右键头像 → 全屏 OCR 定位「拍一拍」→ 点击」，实测 3.2s 发出
  （网络正常时对方收到，网络异常时微信显示失败提示，链路本身正确）。
- `EmojiMessage.capture()` / `voice_call` / `poke` 失败均自动回退或返回
  WxResponse 失败，不影响既有 OCR 发送路径。

### v1.0.8（2026-08-13）
- 🎉 **特别感谢 [vesio](https://github.com/vesio)**：在 issue #1 中提供了微信 4.1.12 可出 UIA 控件树的代码与调试思路，本版 UIA 混合驱动由此而来；
- **UIA 混合驱动**（`uia_driver.py`，微信 4.1.12.26 实测）：
  - 新增 `WeChatUIA` 引擎：冷启动时 `Qt51514QWindowIcon` 只是空壳（Qt
    无障碍门未激活），通过写 Weixin.dll 内的 Qt accessibility gate
    （RVA 扫描定位）**热激活**后，锚点变为 `mmui::MainWindow`，搜索框
    `mmui::XValidatorTextEdit` / 搜索下拉 `search_list` / 输入框
    `chat_input_field` 全部可用；
  - 发送链路全部走 UIA：搜索下拉选人（`search_item_*`）打开会话 →
    `chat_input_field` 直接输入 + 回车发送，`current_chat` 校验防误配，
    无 OCR 抖动；Windows 冷状态热激活后 UIA 树保持可用；
  - `guia.py` 集成混合路径：`_get_uia()` 惰性启用，`open_chat` /
    `send_msg` **UIA 优先、OCR 兜底**——UIA 树不可用（版本变更新增 RVA）
    或失败时自动降级到坐标 + 放大 OCR 方案，首次失败本次会话内不再重试。
  - 实测：`send_msg('文件传输助手')` 10.2s、`send_msg('卢立竺')` 13.2s
    均走 UIA 并数据库确认成功（含 verify）；UIA 对生僻字会话名不再依赖
    OCR 识别。
- 新增依赖：`uiautomation`（UIA 客户端库）。

### v1.0.7（2026-08-13）

- **OCR 识别可靠性提升**（`guia.py`，针对生僻字/小字号会话名识别失败）：
  - 新增 `ocr_zoomed()`：对区域放大 N 倍后再 OCR，坐标按 1/N 还原；实测
    微信小字号中文在放大 3 倍时识别率最高（放大 6 倍图像过大反而整块
    返回空），超过 5 倍即回落；
  - `_chat_is_open` 标题检测改用放大 3 倍 + y 范围扩到 0-185（微信 4.x
    标题实际渲染在 y≈80-180，原 15-100 的区间会漏检已打开的会话）；
  - `_search_chat` 搜索回退排除「群聊」节标题以下行、含「包含」的群成员
    预览行（如「00，包含：卢立竺」）与群名结尾行，只点联系人，修复
    「搜索选中群聊而非联系人」的问题；
  - `_chat_open_confirmed` 改为**优先标题命中**，标题读不到才退而用面板
    非空白作为已打开判据，修复「点错会话也误判成功」；
  - `open_chat` 首查 `_chat_is_open && _pane_has_content`，右侧面板已打开
    目标会话时直接成功（不再滚动/搜索），已打开场景耗时 45s → 2.7s。
- **OCR 多轮投票**（`find_session._scan_vote`）：WinRT OCR 对生僻字存在
  抖动（同一行不同轮次可能读出「卢立竺」或「亠人五」）。对侧栏放大 3x
  扫描 4 轮，命中行按 y 聚类（≤30px 视为同行），票数 ≥2 才返回，显著
  降低误配；普通会话仍走单轮快速路径，无性能损失。
- 实测：`find_session('卢立竺')` 连续 5 轮 4/4 票一致、稳定命中；
  `open_chat` + `send_msg` 全链路成功。

### v1.0.6（2026-08-11）

- **元数据与门面优化**：README 增加徽章（PyPI 版本/下载量/Python 版本/License/Stars）、PyPI description/keywords/classifiers SEO 优化、Homepage 修正为项目 GitHub 地址。

### v1.0.5（2026-08-10）

- **表情截图跨机器修复**：`EmojiMessage.capture()` 表情气泡自动裁剪全面重构：
  - 主路径改用**连通域分析**（`_crop_last_bubble`），按消息方向（左=对方/右=自己）
    精确定位最后一条消息气泡，自动过滤细长竖条（滚动条/面板边框）、剔除头像类
    小元素，从根源解决右缘滚动条/边框被当成内容导致的右侧大片空白；
  - 圆形表情顶部/底部在缩放采样时因 LANCZOS 模糊丢失边缘像素：加大裁剪边距
    （`pad = max(10, scale*5)`）并在全分辨率下**逐像素边缘扩展**找回丢失内容，
    且扩展遇**连续空白行**（消息间分隔）即停，避免吃进相邻消息；
  - 时间戳等居中小文字（水平居中约 50% 宽度）不再被误当成消息：方向判定加
    阈值（左侧 <45% 宽度、右侧 >55%），居中元素两边都不匹配；
  - 最终尺寸校验：`min(crop) < 50` 视为时间戳/文字误判，自动回退到
    「消息分隔空白」「头像锚点」等备用定位，仍过小则判定失败返回 None；
  - 本机与高 DPI 机器均已实测通过（完整表情、无空白、无切顶、不截时间戳）。

### v1.0.4（2026-08-10）

- **多特征兜底窗口定位**：主窗口定位不再只依赖类名 `Qt51514QWindowIcon`
  （类名降级为软条件），联合 进程名 `weixin.exe` / 窗口可见 / 大尺寸
  （≥800px）/ 标题关键词（微信/Weixin/WeChat）评分定位——Qt 升级改名
  （`Qt51514` → `Qt6xxx`）也不失效；渲染子窗口按前缀 `MMUIRenderSubWindow`
  匹配（兼容 `MMUIRenderSubWindowHW` / `MMUIRenderSubWindow` 等变体），
  找不到时退回用主窗口矩形计算坐标。
- **布局自动校准**：首次运行自动校准——OCR 检测「搜索」「发送」锚点实测
  布局比例，保存到 `~/.wechatauto/layout-<机器标识>.json`，之后自动加载；
  布局漂移（DPI/窗口尺寸/缩放变化）时自动重新校准。
- **最大化状态保持**：激活窗口时先 `GetWindowPlacement` 记录状态，原为
  最大化则用 `SW_SHOWMAXIMIZED` 恢复（原 `SW_RESTORE` 会把最大化窗口
  缩成普通大小），最小化恢复不再破坏用户窗口布局。
- **发送模块窗口兜底**：`find_main_window` 类名查找失败后按标题「微信」
  兜底，适配类名不同的机器。

### v1.0.3（2026-08-08）

- **文本消息还原**：微信 4.x 部分文本消息 content 为「容器头 + UTF-8 明文 +
  尾部填充」结构，此前显示为 `[文本]`/空。新增 `_extract_text_from_blob`
  还原明文，数据库读取与 bot 均可见真实内容（含群消息 `wxid_xxx:` 前缀）。
- **表情截图方向感知与兼容性**：`_db_row_to_message` 写入 `msg.attr`
  （`self`/`friend`），`EmojiMessage.capture()` 按方向定位气泡（自己发的用
  消息分隔空白、对方发的用头像锚点），避免截图前自己又发了一条消息时误截到
  自己的气泡；裁剪阈值自适应截图尺寸，跨分辨率/DPI 可用。微信 4.x 主窗口为
  Qt 自绘渲染，不暴露 UIA 子树，故截图定位全部基于屏幕像素分析。
- **发送会话复用**：`send_msg` 记录 `_current_chat`，目标会话已打开时跳过
  `open_chat`（重扫侧栏+点击），逐条连续发送不再反复点击对话框，效率提升。
- **搜索联系人选第一条**：`_search_chat` 按视觉顺序排序并过滤「搜索网络结果/
  搜一搜」节标题，点选第一条联系人而非网络搜索。
- **动画表情不再落盘伪 `.gif`**：`download_image` 识别到 `wxgf` 容器（微信
  动画表情私有格式）时返回 `None`，不再生成打不开的假图片。
- **`Listener.stop()` 崩溃修复**：`db.py` 补 `import sys`（`_run/_poll_once`
  使用 `sys.stderr` 却未导入）。

### v1.0.2（2026-08-08）

- **表情消息支持**：新增 `EmojiMessage` 消息类型（`type='emotion'`），
  "动画表情"不再被归为 `OtherMessage`，并按收发方向提供
  `FriendEmojiMessage` / `SelfEmojiMessage`。微信 4.x 表情消息在本地数据库中的
  content 为加密数据，无法直接还原成图片，因此新增 `EmojiMessage.capture()`：
  采用「打开会话 → 滚动到底 → 截取消息区 → 自动裁剪最后一条消息气泡」
  的屏幕截图方案，返回图片路径，可直接供 AI 视觉识别使用
  （示例见 `demo_emoji_capture.py`）。
- **监听器并发工作线程**：`Listener` 回调移到独立工作线程执行，每个被监听
  会话对应一条**串行**工作线程——同一会话内消息按序处理、不同会话间并行；
  轮询线程只负责读取数据库并分派任务，不再被慢回调（AI 调用 / 图片识别等）
  阻塞，`stop()` 优雅关闭所有工作线程。
- **数据库消息兼容增强**：`_db_row_to_message` 支持 bytes 类型 content
  （自动解码还原文本）、`local_type` 缺失时自动推导消息类型，
  `_extract_group_sender` 兼容 bytes 内容。

---

## 一、项目状态

| 能力 | 状态 | 实现方式 |
| ---- | ---- | -------- |
| 读取消息 | ✅ 已完成并验证 | 本地数据库解密（`wechatauto/db.py`） |
| 消息监听（轮询） | ✅ 已完成并验证 | `Listener` + `get_new_messages` 增量回调 |
| 表情消息识别与截图 | ✅ 已完成并验证（v1.0.3 方向感知） | `EmojiMessage` + `capture()`（屏幕截图自动裁剪） |
| WAL 增量合并 | ✅ 已修复并验证 | 帧盐校验合并 `-wal`（见 §2.4） |
| 历史消息全量导出 | ✅ 已完成并验证 | `export_history`（JSON / SQLite） |
| 媒体下载（图片/语音/文件） | ✅ 已完成并验证 | `wechatauto/media.py`（图片 V2 解密） |
| 下载原图（非缩略图） | ✅ 已完成并验证 | `MediaDownloader.download_image_original()`：UI 点击触发下载 |
| 朋友圈读取 | ✅ 已完成并验证 | `MomentDB` 直接读 `sns.db` |
| 多账号管理 | ✅ 已完成并验证 | `list_accounts()` + `account=` 参数 |
| 读取会话列表 | ✅ 已完成并验证 | 同上 |
| 搜索联系人 | ✅ 已完成并验证 | 同上 |
| 发送消息 | ✅ 已完成并验证 | UIA + 坐标+OCR 混合（`wechatauto/guia.py`） |
| 发送文件/图片/回复/艾特 | ✅ 已完成并验证 | 剪贴板 CF_HDROP + OCR |
| 语音通话 / 拍一拍 | ✅ 已完成并验证 | UIA 按钮 + OCR 菜单（`Chat.VoiceCall` / `Chat.Poke`） |
| UI 自动化（UIAutomation） | ✅ 热激活后可用 | 写 Weixin.dll Qt accessibility gate，物化 `mmui::*` 树 |

**结论**：微信 4.1.x 聊天界面使用自绘渲染（`MMUIRenderSubWindow*`），冷启动
对 UIAutomation 只暴露 `Qt51514QWindowIcon` 空壳（原 wxauto 的 UI 方案因此
失效）。本项目通过**热激活 Qt accessibility gate**（写 Weixin.dll 内读屏
标志位，从 `qt.accessibility.core` 引用扫描 RVA）物化 `mmui::*` UIA 树，
实现发送/语音通话/拍一拍等操作（UIA 优先、坐标+OCR 兜底）；消息读取仍走
「**本地数据库解密**」（已全链路验证）。

---

## 二、读取原理

微信 4.x 的数据存放在本地 SQLCipher 4 加密的 SQLite 数据库中：

```
D:\微信文件\xwechat_files\<wxid>_xxxx\db_storage\
├── contact\contact.db            联系人（昵称、备注）
├── session\session.db            会话列表（未读数、摘要）
├── message\message_0..4.db       聊天消息（按会话分表 Msg_<md5>，跨分库分片）
├── message\media_0.db            语音（VoiceInfo.voice_data，SILK 二进制）
├── message\message_resource.db   文件原名（MessageResourceDetail.packed_info）
├── sns\sns.db                    朋友圈（SnsTimeLine，SnsDataItem XML）
└── ...
```

### 2.1 密钥提取（进程内存只读扫描）

每个数据库有**独立的 32 字节密钥**，保存在微信进程内存中的
`com.Tencent.WCDB.Config.Cipher` 配置对象里：

1. 在 Weixin.exe 所有可读内存区域中查找该字符串；
2. 由字符串地址定位配置对象（`[ptr][len]` 结构回溯）；
3. 数据块与固定掩码异或后得到 `x'<64位hex密钥><32位hex盐>'` 明文配置；
4. 用 SQLCipher 4 HMAC 校验规则验证每个候选密钥；
5. 验证通过的密钥保存到 `%TEMP%\wechatauto_db\<账号>\keys.json` 缓存。

### 2.2 数据库解密

- SQLCipher 4，页大小 4096，`PBKDF2-HMAC-SHA512`（加密密钥 256000 次迭代）；
- 解密结果按页写入临时目录，校验源 mtime/size 复用缓存；
- 首次解密 contact.db 约 6s，之后全部秒级。

### 2.3 消息查询

- 会话名 → `Md5(会话微信号)` → 表名 `Msg_<md5>`（同一会话可能分片在多个
  `message_*.db`，按 `sort_seq` 合并排序）；
- 关键列：`local_type`、`real_sender_id`（2=自己，其他为数字 id，可通过
  `message_resource.SenderName2Id` 反查微信号）、`server_id`、
  `packed_info_data`（图片/视频 md5）、`sort_seq`。

### 2.4 WAL 增量合并（已修复）

微信 `-wal` 是预分配文件：checkpoint 时 WAL 头 salt+1 并清零写游标，但
**旧世代帧仍留在文件中**。若合并时不过滤帧盐，会把过期页覆盖进主库导致
`database disk image is malformed`。修复方案：

- `_merge_wal` 读取 WAL 头后**仅合并 salt 与当前 WAL 头一致的帧**，
  旧世代帧直接跳过；
- 缓存 stamp 加入版本号 `STAMP_VERSION=2`，旧损坏缓存自动强制全量重建；
- 合并结果用 `PRAGMA integrity_check` 校验，失败自动重试全量重建。

验证：contact.db 合并后 integrity OK，2354 个联系人全部可查。

### 2.5 媒体存储与解密（图片 v2 格式）

- 图片：`msg\attach\<会话md5>\<YYYY-MM>\Img\<md5>.dat`（加密）；
- 语音：`media_0.db` → `VoiceInfo.voice_data`（SILK 明文 BLOB）；
- 文件：`msg\file\<YYYY-MM>\<原文件名>`（原名来自 message_resource）；
- 视频：`msg\video\<YYYY-MM>\<id>.mp4`（未落盘时返回 None）。

图片 `.dat` 为 **v2 格式**：`[6B sig 070856320807][4B aes_size LE][4B xor_size LE]`
+ AES-ECB 密文 + 明文段 + 异或段：

- **AES 密钥**：16 字节 ASCII，账户级稳定密钥，但仅在微信查看图片时驻留
  进程内存。`MediaDownloader` 通过内存扫描反测（AES 解首块后校验 JPEG/PNG
  魔数）获取，**命中后持久化到 `image_keys.json`**；也支持 `image_key=` 参数
  显式注入。本机实测：单一密钥稳定解密 35/40 张随机图片（其余为微信动画
  表情容器 `wxgf`）。
- **XOR 密钥**：单字节，从同图缩略图 `<md5>_t.dat` 尾部 JPEG 结束标记
  `FF D9` 反推（`key = tail[0] ^ 0xFF`）。

---

## 三、快速开始

> 📖 **完整使用指南**：[GUIDE.md](GUIDE.md)（中英对照 / bilingual）

### 3.1 安装

```bash
pip install -e .
# 坐标+OCR 发送路线额外依赖：
pip install winsdk pypinyin
```

### 3.2 示例程序

```bash
python demo_db.py
```

### 3.3 代码示例

```python
from wechatauto import WeChatDB

db = WeChatDB()  # 自动检测账号与数据目录（微信需已登录）

info = db.get_self_info()                     # 当前账号昵称
for s in db.get_sessions(limit=10):           # 会话列表
    print(db.get_nickname(s["username"]), s["unread"])

hits = db.search_contact("Ayi")               # 搜索联系人
who = hits[0]["username"]
for m in db.get_messages(who, limit=10):      # 最近消息
    print(m["create_time"], m["sender_id"], m["type"], m["content"])
```

### 3.4 媒体下载

```python
from wechatauto import WeChatDB, MediaDownloader

db = WeChatDB()
md = MediaDownloader(db)                      # 可传 image_key="..." 注入图片密钥
key = md.detect_image_key()                   # 内存扫描/缓存取 AES+XOR 密钥
print(key)

for m in db.get_messages("filehelper", limit=50):
    out = md.download_media("filehelper", m["local_id"])   # 按类型自动分发
    if out:
        print("已下载:", out)
```

### 3.5 朋友圈读取

```python
from wechatauto import WeChatDB, MomentDB

md = MomentDB(WeChatDB())
for feed in md.get_moments(limit=10):          # 时间线（3382 条全量可读）
    print(feed["nickname"], feed["text"])
    print("  图片:", [i["md5"] for i in feed["images"]])
    print("  赞:", [l["nickname"] for l in feed["likes"]])
    print("  评论:", [(c["nickname"], c["content"]) for c in feed["comments"]])
    md.download_media(feed["images"][0])       # 本地缓存或 URL 拉取
```

### 3.6 消息监听

```python
from wechatauto import WeChatDB
from wechatauto.db import Listener

db = WeChatDB()
lst = Listener(db, interval=1.0)
lst.add_listener("filehelper", lambda msg, lst: print("新消息:", msg["content"]))
lst.start()
# ... 业务代码 ...
lst.stop()
```

- 回调在**独立工作线程**中执行（v1.0.2）：每个被监听会话对应一条串行
  工作线程，同一会话内消息按序处理、不同会话间并行；轮询线程只负责读取
  数据库并分派任务，不会被慢回调（AI 调用 / 图片识别等）阻塞。

### 3.7 历史导出

```python
db.export_history(r"D:\backup\chat.json",   fmt="json")    # 全部会话
db.export_history(r"D:\backup\chat.db",     fmt="sqlite")
db.export_history(r"D:\backup\one.json",    fmt="json",
                  users=["filehelper"], limit_per_chat=1000)
```

### 3.8 多账号

```python
from wechatauto import list_accounts, WeChatDB
for a in list_accounts():
    print(a["account"], a["wxid"])
db2 = WeChatDB(account="wxid_xxx_abcd")       # 显式指定账号（缓存按账号隔离）
```

### 3.9 表情消息与截图

微信 4.x 的"动画表情"消息在本地数据库中 content 为加密数据，无法直接还原成
图片。v1.0.2 起监听回调中的表情消息为独立的 `EmojiMessage` 类型
（`type='emotion'`，`FriendEmojiMessage` / `SelfEmojiMessage` 按收发方向区分），
并支持对屏幕上的表情气泡自动截图：

```python
# 在 Listener 回调内，把消息 dict 转成消息对象后再截图：
def on_msg(msg, listener):
    if msg["type"] == "动画表情":
        from wechatauto.wx import _db_row_to_message
        m = _db_row_to_message(msg, chat)   # chat: 当前会话
        path = m.capture()                  # 返回 PNG 路径，供 AI 视觉识别
```

`capture(save_dir=None)` 流程：打开会话（已打开则跳过，避免刷新消息列表导致
控件失效）→ 滚动到底 → 截取消息区 → 按消息方向定位最后一条消息气泡：

- **自己发的消息**（`attr='self'`，右侧无头像）：用「消息分隔空白」定位
  消息顶部，空白阈值按截图高度自适应（约消息区高度的 2.5%），
  跨分辨率/DPI 保持一致；
- **对方发的消息**（`attr='friend'`，左侧有头像）：优先检测头像圆形彩色块
  的顶部作为消息顶部（特征跨分辨率稳定），失败时回退消息分隔空白。

返回图片路径（失败返回 None）。独立示例：`python demo_emoji_capture.py`。
调试时可保留 `~/pane_diag_raw.png`（每次截图保存的消息区原图）与
`[CAP]` 日志行（截图尺寸、消息方向、裁剪路径、结果尺寸）用于排查。

---

## 四、API 参考

### `WeChatDB(db_dir=None, keys_file=None, workdir=None, account=None)`

| 方法 | 说明 |
| ---- | ---- |
| `get_self_info() -> dict` | 当前账号（username / nick_name / remark） |
| `get_sessions(limit=100)` | 会话列表：username / unread / summary / last_time |
| `search_contact(keyword)` | 按昵称/备注/微信号搜索 |
| `get_messages(user, limit, offset)` | 读取指定会话消息 |
| `get_message_row(user, local_id)` | 单条原始消息（含 server_id / packed_info，媒体用） |
| `get_new_messages(user, since_seq)` | `sort_seq > since_seq` 的增量消息（升序） |
| `get_nickname(user)` | 微信号 → 显示昵称 |
| `list_message_chats()` | 所有含消息的会话（md5 / 昵称 / 消息数） |
| `export_history(out_path, fmt, ...)` | 全量导出 JSON / SQLite |
| `extract_keys()` | 手动触发密钥提取 |
| `wxid` / `account` / `account_dir` | 当前账号信息 |
| `list_accounts()`（模块级） | 扫描本机所有微信账号 |
| `auto_detect_db_dir()`（模块级） | 自动定位数据目录（配置文件 → 注册表 → 常见默认目录） |

### `MediaDownloader(db, save_dir=None, image_key=None)`

| 方法 | 说明 |
| ---- | ---- |
| `detect_image_key(refresh)` | 取 (AES 密钥, XOR 密钥)，命中后持久化 |
| `decrypt_image(dat_path)` | 解密单个 `.dat`（自动识别 v1/v2） |
| `download_media(user, local_id)` | 按类型分发下载 |
| `download_image / _voice / _video / _file` | 各类媒体下载 |
| `copy_files_to_clipboard(paths)` | CF_HDROP 写剪贴板（发送附件用） |

### `MomentDB(db)`

| 方法 | 说明 |
| ---- | ---- |
| `get_moments(limit, offset, username)` | 朋友圈时间线（最新在前） |
| `get_moment(tid)` / `get_my_moments(limit)` | 单条 / 我的动态 |
| `find_local_media(md5, kind)` | 本地缓存查找（Sns\Img / Sns\Video） |
| `download_media(media, save_dir)` | 缓存优先，否则 URL 拉取 |

### `Listener(db, interval, watermark)`

`add_listener(user, cb)` / `remove_listener` / `start` / `stop` / `watermark`。

### `WeChatGUI`（发送，锁屏不可用）

| 方法 | 说明 |
| ---- | ---- |
| `send_msg(text, who, verify)` | 文本发送（OCR 定位 + 剪贴板粘贴） |
| `send_file(path, who, verify)` | 文件（CF_HDROP 粘贴 + 回车） |
| `send_image(path, who, verify)` | 图片（同上） |
| `reply_msg(text, who, verify)` | 回复最近消息（悬停 + OCR 回复入口） |
| `at_member(member, text, who, verify)` | 群聊 @ 成员 |
| `open_chat / focus_input / bring_to_front` | 基础操作 |

一行式：`quick_send` / `quick_send_file` / `quick_send_image` / `quick_reply`。

---

## 五、已知限制

1. **需要微信登录**：数据库密钥存于进程内存，首次使用需微信运行中
   （提取后本地缓存）；重新登录后密钥变化需重新提取（自动校验失败重扫）；
2. **图片 AES 密钥瞬态**：仅在微信查看图片时驻留内存；`MediaDownloader`
   扫描命中后会持久化（`image_keys.json`），也可用 `image_key=` 显式传入；
3. **发送为 GUI 操作**：锁屏/会话断开时 `desktop_available()` 返回 False，
   发送接口返回明确失败；文件/图片/回复/艾特代码已完成但需桌面解锁后实测；
4. **视频文件未落盘时不可下载**：视频 mp4 仅在本地存在（`msg/video`）时
   返回，否则返回 None；
5. **发朋友圈功能已舍弃**：4.x 的发表为自绘界面操作，不可靠自动化；
   本库仅保留朋友圈读取/点赞/评论能力。

---

## 六、发送消息（坐标 + OCR）

微信 4.1.12+ 聊天界面自绘渲染、无无障碍节点，发送走
「屏幕坐标 + 本地 OCR」（`wechatauto/guia.py`）：

1. **多特征兜底定位**主窗口（类名 `Qt51514QWindowIcon` 只是「软条件」，
   联合标题 / 进程名 `weixin.exe` / 可见 / 大尺寸评分，Qt 升级改名也不
   失效），再按前缀 `MMUIRenderSubWindow` 找渲染子窗口（兼容
   `MMUIRenderSubWindowHW` / `MMUIRenderSubWindow` 等不同版本类名；
   找不到时回退用主窗口矩形计算坐标）；
2. 布局用渲染子窗口相对坐标描述，运行时换算为屏幕绝对坐标；首次运行自动
   校准（OCR 检测「搜索/发送」锚点实测比例），保存到
   `~/.wechatauto/layout-<机器>.json`，之后自动加载、布局漂移自动重校准；
3. OCR 识别会话列表点击目标（失败走搜索框；生僻字/小字号会话名自动放大
   3 倍 + 多轮投票重扫，搜索回退只点联系人、自动排除群聊与群成员预览行）；
4. 扫描输入框白色区定位并聚焦；
5. 文字以「剪贴板 + Ctrl+V」输入（避免中文输入法拦截），失败回退拼音组合；
6. OCR 定位「发送」按钮（找不到回退回车键）；
7. `verify=True` 时用 `WeChatDB` 读回确认。

文件/图片通过 **CF_HDROP 剪贴板 + Ctrl+V** 插入草稿再回车发送，绕开自绘
「+ 菜单」定位；回复/艾特分别走悬停 OCR 工具栏与成员弹层 OCR。

```python
from wechatauto.guia import quick_send, quick_send_file
quick_send('你好', '文件传输助手', verify=True)
quick_send_file(r'D:\资料\报告.pdf', '文件传输助手')
```

> 注意：OCR 需要系统语言包含中文（`Windows.Media.Ocr`）。

---

## 七、后续路线

1. **发送功能实测**：桌面解锁后校准 guia 各坐标常量，验证文件/图片/回复/艾特；
2. **视频消息下载增强**：微信 4.x 聊天视频存储位置仍需确认（本机无样本）；
3. **性能优化**：导出/首扫并行化，内存扫描增量缓存。

---

## 八、目录结构

```
├── wechatauto/
│   ├── wx.py            UIA 自动化入口（4.x 受限）
│   ├── guia.py          ★ 坐标+OCR 发送模块（文本/文件/图片/回复/艾特）
│   ├── db.py            ★ 数据库读取（密钥提取 + 解密 + WAL 合并 + 导出 + 监听）
│   ├── media.py         ★ 媒体下载（图片 v2 解密 / 语音 / 视频 / 文件）
│   ├── moment.py        ★ 朋友圈（MomentDB 数据库路线 + 旧 UIA 兼容）
│   ├── ui/              UI 控件层
│   ├── msgs/            消息模型
│   └── ...
├── demo.py              UI 自动化示例（微信 4.1 上受限）
├── demo_db.py           ★ 数据库读取示例（推荐）
├── demo_guia.py         ★ 坐标+OCR 发送示例
├── demo_listen.py       ★ 实时消息监听示例
├── demo_reply_at.py     ★ 回复/@ 成员实测示例
├── demo_emoji_capture.py ★ 表情消息截图示例
├── docs/技术文档.md      ★ 完整技术文档（架构/原理/API/扩展）
└── pyproject.toml
```

## 九、免责声明

本项目仅用于个人学习与自动化研究，请遵守微信软件许可协议及当地法律法规，
勿用于任何违反规定的用途。


注：本库完全由AI（opencode+deepseek-v4-flash）生成

---

## 十、联系方式

- 邮箱：fanyuantaier@163.com
