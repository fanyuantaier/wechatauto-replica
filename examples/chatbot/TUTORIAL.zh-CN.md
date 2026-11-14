# 从零安装与配置聊天机器人

本教程对应本目录的厂商中立公开示例。你需要自己选择兼容 `chat/completions` 协议的模型服务；示例不提供模型账号、密钥、真实群或私人素材。

> **先在专用测试群验证，再用于真实会话。** 参与者的文字、图片、昵称及语音转写可能发送给模型服务，本地日志和记忆也可能保存聊天数据。请取得授权并告知参与者。不要上传运行目录、密钥或聊天记录。

## 1. 准备条件

- Windows 10/11，64 位 Python 3.11 或 3.12。
- 官方 PC 微信 4.x 已安装、登录，桌面保持可交互。锁屏、断开远程桌面或注销后，界面发送不保证可用。
- 模型服务的接口地址、API 密钥、文字模型名称。视觉功能还需要支持图片输入的模型。
- 一个获授权的测试群。推荐先只监听这一个群。
- 足够的磁盘空间；基础代码较小，ASR/OCR 的依赖和模型可能占数百 MB 至数 GB。

本教程的模型名称和身份标记都是占位符，不能直接照抄使用。项目虽然是厂商中立配置，但没有验证每种模型服务的兼容性。

## 2. 安装

将仓库下载或克隆到你选择的目录，在**仓库根目录**打开 PowerShell：

```powershell
python -c "import sys,struct; print(sys.version); print(struct.calcsize('P')*8)"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pip install pypinyin
.\.venv\Scripts\python.exe -m pip install -r examples/chatbot/requirements.txt
cd examples/chatbot
..\..\.venv\Scripts\python.exe init_local.py
```

第一条命令应输出 64 位及支持的 Python 版本。后续命令必须使用同一个虚拟环境解释器。

为缩短下文命令，在当前 PowerShell 设置：

```powershell
$Python = (Resolve-Path ..\..\.venv\Scripts\python.exe).Path
```

下文的 `& $Python ...` 都在 `examples/chatbot/` 目录执行。重开 PowerShell 后重新设置 `$Python`。也可以激活虚拟环境后使用 `python`；不要误用另一套系统解释器。

初始化创建 `config.json` 和本地空表，不覆盖已有文件。不要直接修改 `config.example.json` 保存私人信息；该文件用于公开模板。

## 3. 最小配置：先让文字聊天运行

打开 `config.json`。它必须是合法 JSON：双引号字符串，小写 `true` / `false`，最后一项后不能有逗号，不能写注释。中文请使用 UTF-8 保存。

以下是**合并到现有配置的片段**，不是替换整个文件的完整配置：

```json
{
  "api_key": "",
  "base_url": "https://api.example.invalid/v1",
  "model": "YOUR_TEXT_MODEL",
  "group_name": "我的测试群",
  "extra_group_names": [],
  "group_ids": {},
  "bot_names": ["聊天助手"],
  "owner_wxids": [],
  "private_chat_wxids": [],
  "nickname_map": {},
  "owner_remark": "",
  "reply_only_at": true,
  "reply_decision_enabled": false
}
```

### 必填项

| 字段 | 填什么 | 注意 |
|---|---|---|
| `base_url` | 模型服务的 API 基础地址 | 程序会追加 `/chat/completions`；不要填完整请求路径。示例 `.invalid` 地址不可访问，必须替换 |
| `model` | 服务支持的准确文字模型标识 | 不是模型宣传名称，向服务提供者确认 |
| `group_name` | 微信显示的完整群名 | 必须是本账号能访问的群；主群解析失败会退出 |
| `api_key` | API 密钥，或保持空并使用环境变量 | 推荐环境变量；不要在截图、PR 或日志分享中泄露 |
| `bot_names` | 别人喊机器人的名称 | 与微信 @ 显示名或常用叫法保持一致，不表示自动修改微信昵称 |

在当前 PowerShell 设置密钥并启动：

```powershell
$env:CHATBOT_API_KEY = "在本机填入你的密钥"
& $Python bot.py
```

环境变量优先级高于配置：`CHATBOT_API_KEY`、`CHATBOT_BASE_URL`、`CHATBOT_MODEL` 可覆盖对应字段。当前窗口关闭后临时环境变量消失；不建议把密钥写进公开脚本。计划任务不会自动继承另一个 PowerShell 窗口的临时变量。

注意 PowerShell 可能将直接输入的密钥命令保存到历史。更安全的临时输入方式是：

```powershell
$Secret = Read-Host "输入 API 密钥" -AsSecureString
$env:CHATBOT_API_KEY = [System.Net.NetworkCredential]::new("", $Secret).Password
Remove-Variable Secret
```

密钥仍会以明文存在于运行进程的环境中，不是加密存储；不要分享进程转储或环境变量截图。

首次只开文字、`reply_only_at=true`、`reply_decision_enabled=false`，在测试群中 @ 机器人并发送一句话。确认日志中主群已解析、监听已启动，再观察实际回复。停止程序按 `Ctrl+C`。

修改大多数配置后需要**停止并重新启动 bot**。不要启动第二个实例；单实例互斥和看门狗不能代替你的进程管理。

### 检查配置语法

```powershell
& $Python -m json.tool config.json
```

此命令会打印配置，可能包含密钥：仅在本机查看，不把结果直接发给别人。

## 4. 找到账号、群和联系人标识

群显示名用于查找，身份与库 API 使用 `username`（联系人 `wxid_*`，群 `...@chatroom`）。不能把昵称当管理员身份，也不能把一个数字发送者 rowid 当成固定微信身份。

在已登录微信的本机运行：

```powershell
& $Python -c "from wechatauto import WeChatDB; d=WeChatDB(); print(d.get_self_info())"
& $Python -c "from wechatauto import WeChatDB; d=WeChatDB(); print(d.search_contact('你要查找的备注'))"
& $Python -c "from wechatauto import WeChatDB; d=WeChatDB(); print(d.group_name_to_id('你的测试群名'))"
```

结果可能包含个人信息，只在本地保存。将返回的 `username` 填到相应字段，不使用昵称、群二维码或账号登录密码。重名联系人要核对，不能仅取第一条搜索结果。

### 多群与群改名

```json
{
  "group_name": "测试群甲",
  "extra_group_names": ["测试群乙"],
  "group_ids": {
    "测试群甲": "YOUR_GROUP_USERNAME",
    "测试群乙": "YOUR_OTHER_GROUP_USERNAME"
  }
}
```

`group_ids` 的键是配置中的群显示名，值替换为真实群 username。主群必填；附加群找不到会跳过。即使设置了群 ID，也建议群改名后同步显示名，避免 UI 发送找错会话。

### 管理员与私聊

```json
{
  "owner_wxids": ["YOUR_ADMIN_USERNAME"],
  "owner_remark": "管理员的微信备注",
  "private_chat_wxids": ["YOUR_ADMIN_USERNAME"],
  "nickname_map": {"YOUR_ADMIN_USERNAME": "管理员"}
}
```

四项分别控制权限、管理员私信目标查找、私聊监听和称呼，不能互相替代。管理员须是**发消息的人**的 username；机器人登录账号本人的消息可能被自消息过滤，不适合用来测试管理员命令。请用另一个获授权联系人测试。

未设置 `owner_wxids` 时没人有管理权限。定时推送或建议私信需要有效 `owner_remark`。当前内部管理员私信目标只有一个备注，不应假定多管理员列表意味着自动向每个人推送。普通私聊联系人可加入 `private_chat_wxids`，并在 `nickname_map` 配置其可搜索显示名。

## 5. 回复习惯、聚合与人设

| 字段 | 作用 |
|---|---|
| `reply_only_at` | `true` 时仅响应识别到机器人称呼/@的消息；无明确 @ 文本的媒体可能被忽略 |
| `reply_decision_enabled` | 开启后按消息类别决定是否接话；因此收到消息不等于每条都回 |
| `reply_chance_chat`、`reply_chance_question`、`reply_chance_image`、`reply_chance_voice`、`reply_chance_quote`、`reply_chance_card`、`reply_chance_video` | 各类普通群消息的概率门槛，0 跳过、1 尝试；私聊、明确喊机器人及聚合中的问话可能绕过门槛，不是总发送概率 |
| `at_enabled` | 回复时允许加 @；不是“只在被 @ 时回复”的开关 |
| `at_real_enabled` | 真正 UI @，默认关闭；文本 @ 与引用流程存在区别，建议保持默认 |
| `aggregate_enabled` | 聚合短时间连续文字，降低重复请求 |
| `aggregate_window_seconds` / `aggregate_max_seconds` | 等待续句间隔与整轮最长聚合时间 |
| `max_msg_chars` / `split_threshold` | 目标短消息长度与分条触发阈值；按句界拆，不保证每条硬截到长度 |
| `max_tokens` | 模型输出预算，默认 300；不是历史 token 总量 |
| `history_messages` / `history_reset_gap_seconds` | 带给模型的近期上下文条数与长间隔重置时间 |
| `max_reply_age_seconds` | 丢弃过旧消息，避免网络卡住后回复过时话题 |
| `reply_delay_min` / `reply_delay_max` | 发送前的拟人延迟范围，单位秒 |

视频单独立即处理，不与后续文字混成一轮。引用针对文本或语音转写，引用失败回退普通发送；复杂多人聚合可能主动不引用。

编辑 `prompt.txt` 设定助手用途，编辑 `prompt_style.txt` 设置语气。不要写自己的私人账号、住址或其他参与者资料。`persona_identity` 默认空，不强制改写模型自称；填写后会替换部分身份词，可能影响内容准确性，按需使用。严肃话题护栏默认开启，不建议为玩梗关闭。

## 6. 开启默认关闭的功能

每次只开启一个功能，重启并在测试会话验收后再开启下一个。下面的 JSON 都是局部修改片段。

### 6.1 视觉识图

```json
{
  "enable_vision": true,
  "vision_model": "YOUR_IMAGE_CAPABLE_MODEL",
  "vision_max_tokens": 300,
  "vision_max_side": 1280
}
```

视觉模型必须支持兼容协议的图片输入，沿用同一套服务地址与密钥；当前示例不是为视觉提供独立 API endpoint 的通用路由器。图片会发送给模型服务。文字模型不能看图时不能只打开开关；失败可能降级 OCR，OCR 也没开则不等同看到了画面。服务拒绝视觉后有冷却期，可查看日志，修好配置后重启再测。

### 6.2 OCR 图片文字识别

```powershell
& $Python -m pip install -r requirements-ocr.txt
```

```json
{"enable_ocr": true, "ocr_langs": ["ch_sim", "en"], "ocr_min_score": 0.5}
```

首次初始化可能联网下载模型到 `ocr_models/`，时间和空间开销较大。OCR 只能读图上文字，不等同理解整张图；模型下载失败时检查网络及目录权限。请不要把 OCR 模型打进 PR。

### 6.3 本地语音转文字

```powershell
& $Python -m pip install -r requirements-asr.txt
& $Python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='Systran/faster-whisper-small', cache_dir='models/faster-whisper')"
```

下载命令需要网络，是使用者主动执行的步骤；机器人 ASR 加载本身使用本地文件，不偷偷下载。使用代理或镜像请自行确认来源、许可与完整性。不要将完整模型上传仓库。

**当前代码要求缓存快照布局**，而不是随意把一个 `model.bin` 放在模型根目录：

```text
models/faster-whisper/
  models--Systran--faster-whisper-small/
    snapshots/<snapshot-id>/
      model.bin
      config.json
      tokenizer.json
      ...其他模型文件
```

```json
{"asr_enabled": true, "voice_prefetch": true}
```

```powershell
& $Python asr.py --check
```

检查显示模型可用和加载成功，再重启 bot，用短语音测试。默认 CPU/int8、小模型，语音最多转写前 30 秒。语音读取依赖微信本地落盘，`voice_prefetch` 可能打开会话但不会保证每条都下载成功；失败时不要通过自动播放语音来绕开静音保护。

识别在本地进行，但**识别后的文字仍会送给你配置的对话模型**。不能把它描述为“整条链路绝不联网”。

### 6.4 视频：画面与声音

基础依赖已经包含 ffmpeg 包装器与微信静音支持。常用配置：

```json
{
  "video_fetch_enabled": true,
  "video_mute_wechat": true,
  "video_fetch_timeout_seconds": 90,
  "video_max_mb": 120,
  "video_frames": 6,
  "video_long_seconds": 60,
  "video_long_frames": 12,
  "video_transcribe": true,
  "video_transcribe_seconds": 40,
  "video_transcribe_middle_seconds": 20,
  "video_delete_cache": false
}
```

- 画面理解需开启视觉或 OCR，否则抽帧不等于模型理解了视频。
- 声音理解需 ASR 已安装、模型可用且 `asr_enabled=true`。`video_transcribe=true` 单独设置不够。
- 默认短视频约 6 帧，长视频最多 12 帧；音轨抽开头和中段，分成不超过约 28 秒的小段转写。**这是概览/抽样，不是逐帧逐秒看完。**
- 微信本体没下载时，界面路径可能点缩略图触发下载。只在当前会话、候选时长可识别、静音确认成功等条件满足时尝试；否则回退封面。
- `video_fetch_enabled=false` 可禁止本体抓取路径；`video_mute_wechat=false` 不是“有声播放”开关，未缓存视频会拒绝点开。
- `ffmpeg_path` 可额外配置为本机合法 ffmpeg 可执行文件路径。JSON 路径的反斜杠要写成 `\\`，或使用 `/`。
- `video_delete_cache=true` 会删除已成功取得的**该条消息微信缓存 mp4**。这是实际文件删除，确认理解后再开；不会批量清理其他消息，下载失败残留也不保证立即删除。

测试时观察无意外声音、播放窗关闭和原微信静音恢复。UI 自动化存在微信版本、音频会话重建、DPI 和焦点风险，不能承诺绝对无外放。首次不要在重要会议或播放内容敏感的场景测试。

### 6.5 免费网页搜索

```json
{"web_search_enabled": true, "web_search_max_results": 5, "web_search_timeout": 8, "web_search_cache_seconds": 300}
```

使用 Bing 网页抓取，不需要收费搜索密钥。关键词会发给搜索服务，结果摘要再给对话模型；网络或页面结构变化可能返回空结果。它不是完整浏览器，不保证最新事实一定能查到。

### 6.6 主动发言与免打扰

```json
{
  "proactive_enabled": true,
  "quiet_hours": [1, 8],
  "proactive_idle_min_seconds": 3600,
  "proactive_idle_max_seconds": 7200,
  "proactive_active_chance": 0.05,
  "proactive_active_cooldown_seconds": 600
}
```

闲时可能间隔一至两小时主动说一句；群聊活跃时也可能按概率接话。概率与是否发送受模型输出及上下文影响，不是定时广播。免打扰使用机器本地时钟，主要限制主动发言，**不代表停止被动回复或所有管理员推送**。不希望机器人主动讲话就保持 `false`。

### 6.7 自定义贴纸

只使用你拥有使用权的素材，公开示例不附任何图片。步骤：

1. 将匹配用图片放入 `stickers/raw2/`，建议小尺寸 PNG；文件名例如 `happy.png`。
2. 在官方微信的自定义表情中收藏同款。单纯放文件不代表微信里能发送。
3. 运行 `& $Python label_new_stickers.py --apply`，按提示输入标签；会备份本地标签表。
4. 检查 `stickers/stickers.json` 的 `labels` 与 `labels_preview.json` 一致。
5. 配置并重启：

```json
{"sticker_enabled": true, "auto_sticker_chance": 0.2, "sticker_cooldown_seconds": 240}
```

注册表格式示例（本地文件，不提交）：

```json
{
  "threshold": 0.75,
  "cell_pad": 4,
  "labels": {"开心": ["happy.png"], "加油": ["cheer.png"]}
}
```

`auto_sticker_chance=0` 关闭随机自动附图，但模型可能明确输出合法 `[表情:标签]`；彻底禁止发送需 `sticker_enabled=false`。冷却期间或找不到匹配缩略图会不发。当前面板匹配针对特定微信布局，需要本机校准；提高概率不能修复布局错误。

识别别人发的同款贴纸还需要 `stickers/md5_labels.json` 的**微信消息 XML md5**映射。它不是任意本地图片文件 MD5：用获授权测试会话发送同款，读取该表情消息 XML 中的 md5，确认标签后绑定。可在本地 Python 调用 `sticker_read.bind_md5_label('消息中的32位md5', '开心')`；不熟悉校准时留空，让未知表情走截图识别路径（需视觉/OCR）。

`reply_to_stickers=false` 忽略收到的贴纸；`recognize_sticker_image=false` 禁止未知贴纸截图识别。这些不是发送开关。禁用某张图可从两个标签表移除；公开版没有你的私人禁用名单。素材、映射和备份均不提交 Git。

### 6.8 防睡眠

```json
{"prevent_sleep": true}
```

运行期间阻止系统空闲睡眠，屏幕仍可关闭；不解锁桌面、不阻止你主动睡眠/关机、不让锁屏发送变得可靠。便携设备可能增加耗电，按需开启。

### 6.9 候选词定时推送

先正确配置管理员备注与权限，再设置：

```json
{"slang_push_enabled": true, "slang_push_weekday": 5, "slang_push_hour": 18, "slang_push_minute": 0, "slang_push_only_new": true}
```

星期 0 是周一、5 是周六，时间按本机时区。该功能会从历史聊天整理候选词并私信管理员，可能包含敏感聊天片段；不是自动批准新词。测试可由管理员发送 `#候选黑话`。未设置有效管理员目标时不要开启。

## 7. 本地记忆、别名与命令

`memory_enabled`、`aliases_enabled` 默认开启；不想保存/使用相关信息可关闭。关闭开关**不会自动删除已经保存的数据或历史日志**。记忆记录遵循有限抽取，不应作为人员档案或可靠事实数据库。

| 命令 | 权限与作用 |
|---|---|
| `#菜单`、`#帮助` | 管理员，列出命令 |
| `#状态` | 管理员，查看监听及运行状态 |
| `#自检` | 管理员，检查链路；包含一次真实模型请求，可能产生费用 |
| `#语音状态`、`#查看叫法` | 管理员，查看可用性/称呼表 |
| `#查看记忆 名字` | 管理员，查询当前会话相关记忆 |
| `#记住 名字 内容` | 管理员，手动补充记忆 |
| `#忘掉 名字` | 管理员，删除对应记忆；不清除模型服务侧数据、日志或备份 |
| `#禁用称呼 名字` / `#允许称呼 名字` | 管理员，控制本群自动学习称呼 |
| `#候选黑话` | 管理员，扫描并私信候选词 |
| `#集思广益 建议内容` | 所有人可提出建议；本地保存并尝试私信管理员 |

建议在群中管理本群数据，私聊上下文与群聊不同。别名 JSON 支持 `global`、`groups` 和 `auto` 等结构；不熟悉时使用命令，不在程序运行中重写整个数据文件。文件损坏时保留原件，不贸然覆盖。

命令行工具：

```powershell
& $Python memory_tool.py list
& $Python memory_tool.py show "群友名称"
& $Python memory_tool.py del "群友名称"
& $Python slang_show.py
& $Python slang_candidates.py
```

这些输出也可能包含个人数据，分享前脱敏。程序会创建本地备份并清理部分旧日志；这不是全磁盘清理或隐私删除保证。

## 8. 看门狗、最小化及开机恢复

进程内健康检查负责重建异常监听；`watchdog_once.py` 是进程外单次检查，缺少 bot 时尝试以最小化窗口启动。它不自动安装系统任务。

### 设置 Windows 任务计划程序

1. 运行 `taskschd.msc`，选择“创建任务”。任务名建议 `chatbot-bot-watchdog`（与状态查询一致）。
2. 常规：使用你运行微信的 Windows 用户，选择**仅当用户登录时运行**。不要为了后台运行选择无交互桌面的模式。权限应与微信匹配，不无必要提升权限。
3. 触发器：登录时启动；设置每 1 分钟重复、持续时间无限。避免多个重叠任务。
4. 操作：程序填虚拟环境 `python.exe` 的**绝对路径**；参数填 `watchdog_once.py` 的绝对路径并加引号；起始位置填 `examples/chatbot` 的绝对路径，不加引号。
5. 条件：按需调整电池/空闲限制，避免任务不触发。设置：已有实例运行时不启动新实例。
6. 准备密钥：任务不能依赖其他窗口的临时环境变量。可使用本机 `config.json` 保存密钥（确保仅本人可读、Git 忽略），或为任务用户安全配置环境变量。不要把密钥放到任务参数中。
7. 先打开并登录微信，右键任务“运行”，查看 `logs/watchdog.log` 和实际 bot 进程。

重启后必须登录 Windows 并恢复微信登录/交互桌面。看门狗只看到微信窗口不代表微信登录一定有效，不是自动登录器。失败会限频，不能保证每分钟强制成功。

暂停自动拉起：

```powershell
New-Item data/pause_watchdog.flag -ItemType File -Force
```

先创建暂停标记，再用 `Ctrl+C` 停 bot 做维护。恢复：

```powershell
Remove-Item data/pause_watchdog.flag
```

此命令只删除明确的暂停标记。不要为解决问题批量删除微信目录。注册前先用 `& $Python watchdog_once.py` 测一次；它会真实启动程序，不是 dry run。

## 9. 日志、故障排查与更新

| 现象 | 检查 |
|---|---|
| 启动提示未配置 | 主群、模型、真实 base_url、密钥；环境变量可能覆盖文件 |
| 找不到群 | 完整群名、账号权限、是否重名；核对 group_ids |
| 群里不回复 | `reply_only_at`、`bot_names`、接话概率、消息过旧过滤、是否自己的消息 |
| 权限命令无反应 | 发消息者的 username 是否在 owner_wxids，勿用昵称或 bot 自己测试 |
| 模型 401/403 | 服务地址和密钥授权，不把密钥输出给他人 |
| 模型 429/超时 | 服务限流、网络；适度聚合，勿反复重发导致费用/刷屏 |
| 语音不可用 | 同一虚拟环境、SILK 依赖、完整模型快照路径、ASR 开关 |
| 视觉失败 | 服务是否支持图片、视觉模型名称、密钥、冷却；检查 OCR 是否开启 |
| 视频只有封面 | 未下载、无静音会话、找不到安全落点、候选歧义、超时或文件过大；不强行绕过安全检查 |
| 贴纸不发 | 自定义表情是否已收藏、标签、图片、冷却、阈值和面板布局 |
| 微信没声音 | 检查 logs/wechat_mute_state.json 与相关恢复日志；不要直接删唯一音量快照，先停止播放并安全恢复 |
| 看门狗没拉起 | 暂停标记、任务解释器/工作路径、Windows 登录、微信窗口、启动限频、配置是否存在 |

本地日志位于 `logs/`，包含聊天、模型回复等隐私；崩溃记录位于 `crash_*.log`。上游的数据库密钥缓存还可能位于 `%TEMP%/wechatauto_db/`，不是这个示例 `.gitignore` 就能保护的全局数据。不要打包这些目录。

若需要上游诊断，在仓库环境运行 `& $Python -m wechatauto.diagnose_keys`，先审查结果再公开。不要随意删密钥缓存或以系统管理员身份运行所有命令。

升级前暂停看门狗、停止 bot、私下备份 config/data/prompt，然后更新库并在测试群回归。公开示例使用部分内部 API 和微信 UI 行为，兼容性会随版本变化。**当前上游的真实桌面全链路验证尚未完成**；离线测试通过不能当作所有功能均可用的承诺。

## 10. 公开或提交代码前的隐私检查

```powershell
git status --short
git diff --cached --name-only
git diff --cached
```

只提交源码、模板、教程和测试。不提交 `config.json`、data 运行表、logs、images、models、OCR 模型、贴纸、`.env`、压缩备份或数据库。`.gitignore` 不会自动移除已经跟踪的文件；也不保护提交作者邮箱。不要使用 `git add -f` 绕过忽略规则。

个人提示词、厂商专属名称/接口、联系人例句、路径、截图和 Git 提交身份也可能泄露信息。上游许可证和作者署名必须保留，不能为了匿名化抹去来源。只有在确认数据与贡献授权后才发布。
