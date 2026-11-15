# 从零安装与配置聊天机器人

> [!IMPORTANT]
> **强烈建议使用具备本地文件读取、终端执行和代码检查能力的 AI 编程助手，协助完成本项目部署。** 本项目涉及 Windows Python 环境、官方 PC 微信、模型接口及多项可选依赖，纯手动配置容易遗漏。请将下面的提示词交给 AI，让它结合本教程与实际源码分阶段部署，而不是直接照搬命令。
>
> **部署统一使用 64 位 Python 3.12。默认关闭的功能在首次部署时保持关闭；基础部署完成后，由 AI 询问你是否需要开启某项功能，并在你确认后严格按照仓库教程开启或关闭。**
>
> **AI 辅助不代表自动获得所有操作权限，也不保证部署成功。** 密钥应由你在本机安全填写；微信登录由你自行完成。真实发消息、收费模型请求、点击/播放媒体、安装计划任务及删除缓存，都应在你明确同意后进行。没有本地执行能力的 AI 只能提供操作指导，不能宣称已完成部署。

## 可直接复制给 AI 的部署提示词

复制下面整段提示词给 AI。方括号内容由你按实际情况补充；不确定的项目保留“待确认”，让 AI 先询问。**不要在聊天中填写 API 密钥、密码、验证码或数据库解密密钥。** 联系人和群名也尽量在本机配置，不把真实聊天记录交给 AI。

```text
你是一名谨慎的 Windows Python 部署助手。请协助我部署 wechatauto-replica
仓库内的独立聊天机器人示例 examples/chatbot/，以实际源码和文档为准，
不要凭经验假设接口、配置字段、目录或功能已经可用。

【我的部署要求】
- 仓库来源：https://github.com/fanyuantaier/wechatauto-replica
- 使用的分支或提交：[填写包含 examples/chatbot/ 的分支/提交；待确认]
- 仓库本地路径：[填写；未下载则待确认]
- 允许安装代码、虚拟环境、依赖和模型的目录/磁盘：[填写；待确认]
- Python 版本：必须使用 64 位 Python 3.12，不使用 3.11 或其他版本替代。
- Python 解释器：[填写 Python 3.12 的绝对路径，或由你检查后推荐]
- 官方 PC 微信状态：[已安装并登录 / 需要我自行完成]
- 模型接口 base_url：[填写非秘密的基础地址，或在本机配置]
- 文字模型 model：[填写服务支持的准确模型标识；待确认]
- 视觉模型 vision_model：[如需要则填写；默认不开启]
- 密钥提供方式：我在本机安全输入，不在聊天中发送。
- 获授权的测试群、管理员及私聊：由我在本机填写或确认，不使用示例占位符。
- 可选功能：首次部署一律保持仓库默认关闭的功能关闭；基础部署完成后
  你必须主动询问我是否需要开启某项功能，再等待我明确选择。
- 是否需要登录后自动恢复：[否 / 待确认]

【必须遵守的边界】
1. 先阅读仓库的 AGENTS.md、本示例 README.md、TUTORIAL.zh-CN.md、
   config.example.json、init_local.py、requirements*.txt 与相关源码。
   遵守适用的仓库指引；文档与源码不一致时先说明，不静默猜测。
2. 先检查工作目录和现有修改。不要覆盖我的已有机器人、配置、人设、
   data、贴纸、日志或备份；有现用版本时先询问备份范围和存放位置，
   将新部署与生产目录隔离，不停止或改动现用进程。
3. 不收集或打印 API 密钥、密码、验证码、数据库密钥或完整聊天记录。
   尽量只检查文件是否存在、字段是否已填写，不输出秘密字段的值。
   不把配置、日志、截图、环境变量或运行目录上传外部网站。
4. 不修改全局 Python、系统安全设置或微信客户端；不无必要提升权限。
   安装前说明下载来源、目标路径、预计体积及是否可能写入系统缓存。
   若我的磁盘要求无法完全满足，先说明并确认，不声称“绝不写入其他盘”。
5. 没有明确授权，不发送真实微信消息、不调用收费模型、不点击或播放
   语音/视频、不启用主动发言、不注册计划任务、不删除微信缓存。
   不通过关闭静音保护来强行获取视频，不保证 UI 自动化绝不会外放或误操作。
6. 不擅自 commit、push、发布 PR 或上传部署文件。不要使用破坏性 Git
   操作或批量删除目录来排错；修改前说明范围，保留我的已有工作。
7. 不因依赖已经安装、模型支持某能力或你认为有用而自动启用功能。
   已有配置中的关闭项同样不得擅自开启。只有我明确确认的功能才可调整，
   开启或关闭都必须完全按照仓库 TUTORIAL.zh-CN.md 的对应步骤执行。

【请按阶段执行】
阶段一：检查与计划
- 检查 Windows、Python 3.12 和 64 位架构、磁盘空间、目录权限及已有环境。
  若缺少 64 位 Python 3.12，先说明并请求安装授权，不改用其他版本，
  不卸载或覆盖已有 Python。创建虚拟环境后再次核对其版本与架构。
- 确认所选分支/提交确实包含 examples/chatbot/；如果上游 main 尚无示例，
  停下来询问正确来源，不从不明仓库替代下载。
- 列出缺失条件、准备修改的文件、安装命令和必要授权，先让我确认计划。

阶段二：隔离安装与最小配置
- 使用我确认的 64 位 Python 3.12 解释器创建项目独立虚拟环境；所有安装和运行均使用同一
  解释器，不混用系统 pip。按教程从仓库根目录安装当前库源码、基础依赖
  及 pypinyin；不要擅自安装所有 ASR/OCR 大型依赖。
- 运行 init_local.py 创建缺失配置和空表，不覆盖已有文件。
- 仅在本机 config.json 或当前进程环境中配置秘密，不将私人信息写入
  config.example.json、公开脚本、教程或提交。指导我用不把密钥写进
  PowerShell 命令历史的安全输入方式，并说明环境变量仍是进程内明文。
- 核对 base_url 会追加 /chat/completions、model 为准确服务标识；
  不把不可访问的 .invalid 地址或 YOUR_* 占位符当成真实配置。
- 使用获授权的本地联系人查询核对 username；管理员权限使用发消息者
  的 username，不用昵称、发送者 rowid 或机器人登录账号自身替代。
- 首次仅文字：reply_only_at=true、reply_decision_enabled=false。
  保持主动发言、贴纸、ASR、OCR、视觉、搜索、防睡眠、定时推送和
  缓存删除关闭。若希望严格禁止视频本体抓取，设置 video_fetch_enabled=false。
  解释本地记忆/别名默认开启及其隐私影响，由我决定是否关闭。

阶段三：离线检查与授权验收
- 检查 JSON 合法性时不要打印完整含密钥配置；检查 Python 语法、
  初始化不覆盖数据，并运行 examples/chatbot/tests 中的离线回归。
- 离线检查完成后先报告结果，再询问是否授权启动 bot 和真实验收。
- 经同意后，用专用测试群完成一次受控文字请求，说明会向模型服务
  发送哪些内容及可能费用；观察监听、回复、发送目标和重复实例问题。
- 不把语法/离线测试通过称为真机全链路成功；失败时先定位原因，
  避免反复发消息、调用模型或重启导致刷屏和费用。

阶段四：基础部署完成后询问，再按教程调整功能
- 先交付基础文字部署结果，说明成功、失败及尚未验证的部分，列出当前
  默认关闭的功能；不得为了展示能力预先开启它们。
- 必须主动询问我：“基础部署已完成，是否需要开启某项默认关闭的功能？
  请逐项选择；也可以全部保持关闭，或指定需要关闭的已开启功能。”
- 等待明确答复。未答复、回答不明确或选择不启用时，保持现状，
  不自行修改功能开关、安装可选依赖或下载可选模型。
- 若我需要调整，先重新阅读仓库 TUTORIAL.zh-CN.md 对应章节，
  完全按照教程中的依赖、模型/素材准备、配置字段、重启和验收要求，
  逐项开启或关闭。不要自行发明开关、跳过前置条件或绕过安全保护。
  教程未说明关闭细节或与源码不一致时，先报告并询问，不猜测执行。
- 每次只调整我确认的一项，说明对应教程章节、依赖、开关、数据流、
  成本和关闭方法；完成后报告实际状态，不把配置已开启当成验证已通过。
- 视觉：核对同一接口支持图片输入及 vision_model；图片会发送给模型。
- OCR：说明首次可能联网下载模型到 ocr_models/，OCR 不等于整图理解。
- ASR：按源码准备完整 faster-whisper-small 缓存快照，并做 --check；
  本地识别后的文字仍会发送给对话模型，不宣称整条链路完全离线。
- 视频：说明画面依赖视觉/OCR、音轨依赖 ASR，属于抽样概览；保留
  静音与恢复保护，无法安全抓取时回退封面，不绕过保护。
- 贴纸：只用我有权使用的素材，确认微信收藏、raw2 图片与标签表；
  接收识别使用微信消息 XML md5，不把任意本地文件哈希当作映射。
- 网页搜索/主动发言/候选词推送：先解释外部请求、刷屏和私信风险。
- 看门狗：先手动验证，再经同意注册仅在用户登录时运行的计划任务；
  使用绝对解释器和脚本路径，解释任务不继承其他窗口的临时密钥变量，
  并提供 pause_watchdog.flag 暂停办法。不要承诺锁屏或注销后可发送。
- 缓存删除默认保持关闭，只有明确授权才打开，说明实际删除的文件范围。

阶段五：交付
- 给出实际部署目录、解释器路径、启动/停止命令、配置文件位置、
  已开启和未开启功能、测试结果、限制、日志位置及安全排错方法。
- 给出不含秘密的变更摘要和恢复/升级步骤，标明尚未验证的功能。
- 再次提醒不要公开 config.json、data、logs、媒体、模型、贴纸、
  .env、数据库和私人备份；Git 忽略规则不能保护已经跟踪的文件。
- 如果你没有本地文件或终端工具，明确说明只能指导我执行；请分步给出
  命令并等待我反馈，不编造“已安装”“已启动”或“测试通过”。

现在请先执行阶段一的只读检查；如缺少信息，列出最少必要问题，
给出部署计划，等待我确认后再安装或修改。
```

下面是手动部署与配置说明，也可供 AI 逐项核对。请始终以你实际使用的分支、源码和本机测试结果为准。

本教程对应本目录的厂商中立公开示例。你需要自己选择兼容 `chat/completions` 协议的模型服务；示例不提供模型账号、密钥、真实群或私人素材。

> **先在专用测试群验证，再用于真实会话。** 参与者的文字、图片、昵称及语音转写可能发送给模型服务，本地日志和记忆也可能保存聊天数据。请取得授权并告知参与者。不要上传运行目录、密钥或聊天记录。

## 1. 准备条件

- Windows 10/11，**64 位 Python 3.12**。本教程统一使用 3.12，不使用 3.11 或其他版本替代。
- 官方 PC 微信 4.x 已安装、登录，桌面保持可交互。锁屏、断开远程桌面或注销后，界面发送不保证可用。
- 模型服务的接口地址、API 密钥、文字模型名称。视觉功能还需要支持图片输入的模型。
- 一个获授权的测试群。推荐先只监听这一个群。
- 足够的磁盘空间；基础代码较小，ASR/OCR 的依赖和模型可能占数百 MB 至数 GB。

本教程的模型名称和身份标记都是占位符，不能直接照抄使用。项目虽然是厂商中立配置，但没有验证每种模型服务的兼容性。

## 2. 安装

将仓库下载或克隆到你选择的目录，在**仓库根目录**打开 PowerShell：

```powershell
py -3.12 -c "import sys,struct; print(sys.version); print(struct.calcsize('P')*8); assert sys.version_info[:2] == (3,12) and struct.calcsize('P')*8 == 64, '需要 64 位 Python 3.12'"
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -c "import sys,struct; assert sys.version_info[:2] == (3,12) and struct.calcsize('P')*8 == 64, '虚拟环境必须使用 64 位 Python 3.12'"
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pip install pypinyin
.\.venv\Scripts\python.exe -m pip install -r examples/chatbot/requirements.txt
cd examples/chatbot
..\..\.venv\Scripts\python.exe init_local.py
```

第一条命令必须输出 Python 3.12.x 和 64 位，且检查通过。若失败，先安装或选择 **64 位 Python 3.12**，不要继续执行。若已有 `.venv`，先核对其版本，不覆盖正在使用的环境；版本不符时另建隔离环境。

`py -3.12` 需要 Windows Python Launcher。没有 `py` 命令时，可用已确认的 Python 3.12 可执行文件绝对路径替代，例如 PowerShell 的 `& "你的Python3.12解释器绝对路径" -m venv .venv`，并同样检查版本和架构；不要用版本不明的 `python` 代替。后续命令必须使用同一个 Python 3.12 虚拟环境解释器。

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

**首次部署不自动开启任何默认关闭的功能，也不擅自开启使用者已经关闭的功能。** 基础文字部署完成后，AI 部署助手必须先报告结果和功能状态，再询问使用者是否需要开启某项默认关闭的功能，或关闭某项已开启的功能。没有明确确认就保持现状，不安装额外可选依赖或下载模型。

确认需要调整后，必须完整遵循下面对应章节的安装、准备、配置与验证步骤来开启或关闭，不自行推测开关或绕过安全保护。每次只调整一个功能，按需重启并在获授权的测试会话验收后再调整下一个。关闭配置不等于删除历史数据、模型或日志，不自动执行文件清理。下面的 JSON 都是局部修改片段。

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
