# Chatbot 群聊机器人示例

基于 [wechatauto-replica](https://github.com/fanyuantaier/wechatauto-replica) 的独立 Windows 微信机器人示例。仅使用官方 PC 微信 4.x，不使用网页版。此目录不修改库的公共 API，也不改变库默认行为。

## 能力

- 数据库增量监听多个群和指定私聊，按会话聚合文字、区分当前发言者。
- 兼容 chat/completions 协议的模型接口；不预设厂商、模型名称或真实服务地址，输出默认 300 tokens。
- 可选本地 SILK 语音转写、OCR、视频抽帧及分段音轨识别。
- 引用回复失败后普通发送，短消息拆分，发送确认及前台恢复。
- 本地记忆、别名、管理员命令、Bing 网页搜索、监听健康检查及单次进程看门狗。
- 自备贴纸标签和缩略图匹配（示例不附带任何第三方素材）。

## 快速开始

完整安装、配置填写、各项功能开关、贴纸准备及任务计划程序教程见 **[详细使用教程](TUTORIAL.zh-CN.md)**。第一次使用建议先按教程只启用文字功能。

要求 Windows 10/11、64 位 Python 3.11/3.12、已登录官方微信及可交互桌面。先在**仓库根目录**安装当前库源码：

```powershell
python -m pip install -e .
python -m pip install -r examples/chatbot/requirements.txt
cd examples/chatbot
python init_local.py
```

编辑生成的 `config.json`：

- `base_url`：账户实际可用的接口地址；示例地址不是对可用性或授权的保证。
- `model`：自行选择的文字模型名称；`vision_model` 按需填写，视觉功能默认关闭。
- `api_key`：推荐保持空值，通过 `CHATBOT_API_KEY` 环境变量设置。**不要提交密钥。**
- `group_name`：实际群显示名称；可用 `group_ids` 配置对应 username。库 API 使用 username，不把昵称当身份。
- `owner_wxids`：管理员 username 列表，默认为空，未配置时无人获得管理员权限。
- `private_chat_wxids`、`nickname_map`、`owner_remark`：仅按需填写，不预设任何真实账号或联系人。

```powershell
$env:CHATBOT_API_KEY = "你的接口密钥"
python bot.py
```

模板不含真实群或密钥，未配置时会提示退出，不自动连接或发消息。`prompt.txt` 和 `prompt_style.txt` 是全新通用提示词，可自行修改；`persona_identity` 默认空值，不强制改写身份。可通过 `CHATBOT_BASE_URL` / `CHATBOT_MODEL` 覆盖接口和模型。

**运行前先通知参与者并征得同意**：文本、图像、语音转写、昵称及部分上下文可能发送至你配置的模型服务。记忆、队列、日志和备份也可能保存聊天内容，不能当作匿名数据。仅使用你获授权访问的会话，并遵守平台规则及适用法律。

## 默认安全设置

主动发言、自动贴纸、语音转写、OCR、防睡眠及候选词定时推送默认关闭。微信缓存删除默认关闭。视频下载会驱动微信界面；不能确认静音时不播放，只分析封面。任何 UI 自动化都可能受到版本、DPI、焦点和第三方 COM 异常影响，不能保证绝不外放或绝不误操作。建议先在专用测试会话验证。

`config.json`、`data/` 运行数据、媒体、模型及日志都被目录级 `.gitignore` 排除。初始化脚本只创建缺失文件，**不覆盖已有数据**。不要强制添加这些文件；分享前检查 Git 暂存区，不分享整个运行目录。

## 可选功能

- ASR：`python -m pip install -r requirements-asr.txt`，在本目录 `models/faster-whisper/` 放置合法取得的 faster-whisper 模型，再开启 `asr_enabled`。模型仅本地加载，不随 PR 分发。
- OCR：`python -m pip install -r requirements-ocr.txt`，再开启 `enable_ocr`；首次初始化可能下载 OCR 模型到 `ocr_models/`。
- 视频：依赖 `imageio-ffmpeg` 和 `pycaw`；长视频最多抽 12 帧，音轨按片段抽样，**不是完整听完视频**。
- 贴纸：见 `stickers/README.md`，准备自己的授权素材和映射后再开启。当前匹配布局来自特定微信版本，换布局需要校准。
- 看门狗：`python watchdog_once.py` 为单次检查，**不会自行注册开机启动或计划任务**。可自行将其配置到任务计划程序的已登录交互会话中，每分钟执行；默认使用当前解释器，也支持 `CHATBOT_PYTHON`。创建 `data/pause_watchdog.flag` 可暂停拉起。
- 免费搜索仅抓取 Bing 页面，不提供收费搜索 key；网络、页面结构或地区限制可能导致失败。

管理员命令包括 `#菜单`、`#自检`、`#状态`、`#查看记忆`、`#记住`、`#忘掉`、`#禁用称呼`、`#允许称呼` 等；`#集思广益` 用于收集建议。实际以 `bot.py` 为准。身份只按配置的 username 判断，不按昵称授予权限。

## 验证与限制

```powershell
python -m unittest discover -s tests -v
```

23 项离线回归覆盖精确视频缓存、异常清理、静音快照、数据热加载、原子写入、并发绑定及看门狗。测试不导入 bot 主模块，不发微信消息或请求模型。它们不代替真实桌面端到端测试。

本示例由既有本地应用整理而来，涉及部分库内部接口和 UI 兼容处理。已做脱敏、语法检查与离线测试；**尚未针对当前上游 1.2.10 完成真实微信全链路验证**。不把历史本机测试结果当作上游版本兼容保证。

## 授权与素材

上游库作者及许可证保留，见仓库根目录 [LICENSE](../../LICENSE)（Apache-2.0）。本贡献拟按该仓库的 Apache-2.0 条款提交，最终以作者合并和仓库要求为准。未附带任何私有运行数据、个人提示词、第三方贴纸、预训练模型或依赖二进制；它们各自的许可不由此 PR 授予。
