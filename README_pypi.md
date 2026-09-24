# wechatauto-replica — 微信 4.x Windows 自动化 / WeChat 4.x Automation

> **中文版** 在下方 · **English version below**

---

## 🇨🇳 中文版

### wechatauto —— 微信 4.x Windows 客户端自动化（wxauto 复刻版）

![PyPI version](https://img.shields.io/pypi/v/wechatauto-replica)
![PyPI downloads](https://img.shields.io/pypi/dw/wechatauto-replica)
![Python](https://img.shields.io/pypi/pyversions/wechatauto-replica)
![License](https://img.shields.io/github/license/fanyuantaier/wechatauto-replica)
![GitHub stars](https://img.shields.io/github/stars/fanyuantaier/wechatauto-replica)

> [!NOTE]
> **📢 维护状态 / Maintenance Notice**
> 本人因今年升高一，开学后几乎没有时间继续更新本项目（如果有时间，争取周日更新）。遇到问题请自行在 Issues 区讨论，或询问 AI 协助解决。感谢支持！
>
> I'm starting senior high school and will register tomorrow (Aug 23). After school starts I'll have almost no time to keep updating (Sundays if possible). Please discuss issues in the Issues section or ask an AI. Thanks for your support!


> [!NOTE]
> **📢 维护状态 / Maintenance Notice**
> 本人因今年升高一，开学后几乎没有时间继续更新本项目（如果有时间，争取周日更新）。遇到问题请自行在 Issues 区讨论，或询问 AI 协助解决。感谢支持！
>
> I'm starting senior high school and will register tomorrow (Aug 23). After school starts I'll have almost no time to keep updating (Sundays if possible). Please discuss issues in the Issues section or ask an AI. Thanks for your support!



本项目复刻上游 wxauto 项目，目标是实现对当前微信 4.x Windows 客户端的自动化
（读取消息、发送消息、媒体下载、朋友圈），非网页版，直接操作本机客户端。

> 当前版本：1.2.4
>
> **兼容范围**：Windows 10/11 ｜ Python 3.9+（已在 3.12 验证）｜ 微信 **4.1.12+**（已在 4.1.15.13 验证）
> （数据库读取路线对微信版本不敏感；坐标+OCR 发送路线依赖 4.1.12+ 自绘渲染
> 布局，UIA 控件树随小版本变化——4.1.15 起搜索入口改为收起式，已适配；
> 其它小版本可能需校准 `guia.py` 布局常量与 gate RVA 表）。

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
>
> 感谢 [wenjiavv](https://github.com/wenjiavv) 报告 [issue #28](https://github.com/fanyuantaier/wechatauto-replica/issues/28)（1.2.2.5 缺失 `import threading`，布局校准必抛 `NameError`）与 [issue #29](https://github.com/fanyuantaier/wechatauto-replica/issues/29)（发送回读校验接受包含额外正文的历史消息），两份都附了复现步骤、宽屏/竖屏的不同症状和修复建议（v1.2.2.6 修复）。

---

## 版本记录

### v1.2.4（2026-09-24）

- **适配微信 4.1.15.13**（本轮进行到一半客户端自动升级；`Weixin.dll` 从 198,060,584 涨到 201,552,944 字节）。这里其实是三件独立的事，而且从外面看长得一模一样：
  - **搜索入口改成了默认收起。** 原来的输入框现在只剩一个名叫「搜索」的 `mmui::XButton`（实测 `[314,84,370,140]`），点了它之后 `mmui::XValidatorTextEdit` 才会出现。`_search_box(expand=True)` 会先点一次再等输入框；`search_box_rect()` 在收起状态下仍然给得出坐标（返回按钮矩形，作为**只读锚点**，故意不点——取坐标不该有副作用），`open_chat()` 则显式要求展开。老版本输入框本来就常驻，这时一步都不多点。4.1.13.x 照常可用，走的是哪条分支由离线自检钉住。
  - **gate RVA 挪到了 `0xb135c38`**，已进版本表，常用路径不必再跑 1.2.3 那套向量化扫描（未收录的版本仍然要扫，结果照旧落盘 `~/.wechatauto/gate_cache.json`）。
  - **进朋友圈需要显式唤醒。** `mmui` 树还没物化时，`_switch_to_moments_new_style` 报的是「找不到导航按钮」——这句话听起来跟「这个版本不支持」一模一样，我第一时间就被带偏了。现在会先检查树的就绪状态，调 `ensure_materialized(timeout=6.0)`，**并且唤醒之后重新锚定根控件**（旧句柄指向的是唤醒前的空壳）。
- **修复：朋友圈滚动从来没真的滚过——`find_moment` 只是看起来能用。** `_send_scroll()` 把 `MOVE` 和 `WHEEL` 两个 `SendInput` 连着发，**中间零停顿**。滚轮事件是在**出队那一刻**按光标所在位置投递的，所以它落在了旧坐标上、也就是完全错误的窗口；而且微信不是前台窗口时，mmui 时间线根本不接收滚轮。现在先用 `SetCursorPos` + `GetCursorPos` 轮询确认光标真的落位（最多 8 次约 0.4 秒），再停 0.3 秒，`_scroll()` 之前还会把窗口提到前台。实机：`find_moment` 连跑两次都命中（51.4 秒 / 14.2 秒），后来一次 17.2 秒——改之前是碰运气，而且运气好时看起来像时序抖动。
- **朋友圈点赞/评论合并成一条实现。** 之前有两条并行路线，而演示里真正跑通的那条（定位 「…」 按钮 → 点浮层里的 赞/评论）并不是 `LikeMoment` / `CommentMoment` 走的路。现在 `Like` / `Comment` 先走浮层路线，只有失败才退回老的反向菜单右键；`LikeMoment` / `CommentMoment` 改为委托，不再各自留一份副本。老评论窗口（`MomentCommentDialog.send`：点输入框 → `Ctrl+A` → 粘贴 → 点「发送」）**还漏在 1.2.3 的节流之外**，现在补上了，并且放在所有前置校验之后（前置校验没过就不该占用写动作额度）。实机验证：点赞、评论都落地了，从本地朋友圈库回读也能看到那条新评论。**留下的教训：一个功能两份实现，被审过的那份往往不是正在跑的那份。**
- **修复：`find_moment` 会把一条点不动的 cell 交出去。** cell 滚出视口后 UIA 会回收它的句柄，回收后的节点 `BoundingRectangle` 是 `(0,0,0,0)`，在它上面做任何坐标动作都会抛 `Can not move cursor`——表现出来就是「明明定位成功了，却报未能打开朋友圈操作菜单」。现在命中之后先验再返回：`_rect_usable()`（读不到/退化矩形都算不可用）、`_reattach_item()` 用「昵称 + 正文前缀 + 时间」在当前可见 cell 里重新认领同一条，**原地换 control**（调用方手里的对象继续有效，不必重新拿返回值），`_settle_item()` 在同屏没有可用分身时先把它滚回视野再试一次。认领刻意保守：**绝不会**认领同一个人的另一条（那就等于给别的动态点赞），而既没正文又没时间的只剩昵称、直接拒绝。正文比对用「前 10 字互相包含」而不是精确签名——UIA 摘要会截断正文，DB 校正过的正文反而更长，用精确签名会连自己那条都认不出来（这条是自检先暴露的）。`find_moment` 的两个命中点、`_locate_more_click`（每轮重试前）、`_invoke_action_menu`（动手前）全部过这道检查。
- **修复：全局监听时的一个真实并发崩溃。** 两个线程同时打开同一份库会写到同一个 `dst.tmp` 中间文件，从监听线程里抛出 `FileNotFoundError`（`os.replace`）。`db._open()` 现在按库加构建锁，中间文件名带进程号和线程号（`dst.<pid>.<thread-id>.tmp`），并且清理被杀进程留下的残留（超过 600 秒的 scratch）。
- **修复：`get_messages()` 的分页入参不是失败即空的。** `limit=0` 会返回一整页，`offset=-1` 会返回**最后一条**（Python 切片的行为，不是数据库报错），于是本该报错的调用拿到了一个看似合理的答案——是在实机监听短会话时发现的。现在 `limit <= 0` 或 `offset < 0` 一律返回 `[]`。
- **新增：`AddListenAll()` 真的监听到所有会话了。** 全局回调过去是**按会话**注册的，所以任何已经单独 `AddListenChat` 过的会话都被跳过、永远收不到全局回调；现在按回调去重，轮询中新出现的会话会自动纳管，交给回调的那个伪会话可用了（`nickname` 能取到、`chat` 懒建、`SendMsg` 能发），监听器重启后全局回调会重新挂上，`RemoveListenAll()` 也真的摘得掉。写进 `GUIDE.md` §4.3。**已知代价**（本轮未修）：200 个会话左右的账号上注册约 11 秒、每轮轮询约 10 秒，因为每个会话都要打开再读；这也让它成为 issue #25「IO 偏高」报告的头号嫌疑。
- **实机复验**（真实客户端，`calm` 节奏档，逐项拉开间隔）：向文件传输助手发送并逐字回读通过两次（新 `sort_seq` 精确命中）；`back_to_chat_tab()` + `open_chat()` 端到端通过（整窗像素变化 7.2% / 8.3%，作为「页面确实切了」的客观判据）；朋友圈定位 → 点赞 → 评论，界面与库两侧都确认到位。
- **回归覆盖**：`tools/selftest.py` 新增 `moment` 组 30 项（假控件 **和** 真 `MomentItem` 实例各跑一遍，纯离线、不碰微信也不点击），外加本轮补的 `click`（8）、`listen`（7）两组，整套 **175 项 0 失败**（这一项修复之前是 147）。两处变异验证新组真的会咬：把 `_rect_usable` 改成永远返回 `True` → 15 项红；认领时只比昵称 → 5 项红，而且红的正是「不许认领另一条」那几条。

### v1.2.3（2026-09-22）

- **新增：拟人节奏层 `wechatauto/rhythm.py`，默认开启，会改变默认可见行为。** 本机账号在 2026-09-21 触发过一次风控（会话中途被要求重新登录），此后凡是驱动真实微信界面的动作都必须按真人节奏走，而且要做进库里——做进测试脚本等于只对一份脚本生效。风控看到的是**动作的时间分布**而不是坐标：固定间隔、光标瞬时传送、永远命中控件正中、逐字匀速键入、几秒内连发多条。现在 `nap()` 给所有等待加抖动（倍率下限固定 1.0，只加长不缩短，所以原来靠固定 `time.sleep()` 撑住的渲染稳定性不会被破坏）；`move_to()` 让光标沿二次贝塞尔曲线走过去，`point()` 在控件四边内缩后的区域内随机取落点（不再每次都打在 `BoundingRectangle` 正中）；`type_gap()` / `key_hold()` 打破匀速键入；`gate()` 只管**对外可见的写动作**（发消息、发文件、点赞、评论、撤回、拍一拍、语音通话）的最小间隔 + 滚动窗口突发上限。读路径（读库、截图、OCR、定位控件）一律不节流。档位 `natural`（默认）/ `calm` / `fast` / `off`，`off` 精确还原这层之前的取值，只用于对照实验；环境变量 `WECHATAUTO_RHYTHM`、`WECHATAUTO_WRITE_GAP`、`WECHATAUTO_WRITE_BURST` 单项覆盖。节流状态落盘到 `~/.wechatauto/rhythm.json`，因此**跨进程也生效**：一个演示脚本就是一个新的 Python 进程，纯内存限速对这种用法等于没有。默认档下两次发送之间至少隔 2.5–6 秒，120 秒窗口内第 7 次写动作先进 30–75 秒冷却。跨进程是「最后写入者赢」，两个进程同时 `gate` 时可能都少等一点，这层故意没上命名互斥量。
- **修复：UIA gate 扫描能把 `quick_send` 卡住约 8 秒，还能把整个调用打断。** 有用户反馈卡在 `_rip_xrefs_to_rva` 的逐字节循环里（198 MB 的 `Weixin.dll`，本机实测 6.6–9.8 秒，慢机上像死锁），而这条路径上没有兜底：扫描里任何异常都会一路冒出 `ensure_window`，把 `quick_send` 整个打断。现在扫描改成 numpy 向量化——真实 DLL 上候选与逐字节实现完全相同（`0xae2b0c8`），耗时 0.56–0.77 秒（约 12 倍）；老实现保留，作为缺 numpy 时的回退路径（`numpy` 随 `opencv-python` 一起来，正常装包走不到，真缺了只是慢，不能让 UIA 整条路消失）。扫描结果按 DLL 身份（版本目录 + 大小 + mtime）落盘 `~/.wechatauto/gate_cache.json`，**包含「扫过了、没有候选」这种负结果**（不支持的版本每次都重扫最浪费），同进程二次扫描 0.000 秒；已验证的 gate RVA 也从这里恢复并顶到候选序列首位。读不到文件 / 不是 PE 现在返回空序列而不是 `None`（调用方是直接迭代的），且这类瞬时失败不写盘。热激活里的异常改成「本轮跳过、回落 OCR/坐标」，不再打断发送；`KeyboardInterrupt` 属于 `BaseException`，仍然照常中断，Ctrl+C 不会被吞。
- **修复：朋友圈评论的老路径完全没过节流。** 评论有两条并行实现，上一轮只给新的模板匹配路径（`_click_comment_send`）加了 `gate('comment')`，而 `Moment.Comment` 走的仍是老的 UIA 评论窗口（`MomentCommentDialog.send`）：点输入框 → `Ctrl+A` → 粘贴 → 点「发送」，一步都没节流。现在这条补上了，且放在所有前置校验之后（窗口不存在、正文为空、找不到输入框这些情况不占用写动作额度）。**教训是审计必须按「做了什么」而不是按函数名**：`tools/selftest.py` 现在扫的是动作本身（`SendKeys('{Enter}')` / `press_enter` / `keybd_event` / 点击「发送」），命中却没有 `rhythm.gate` 就报错，另附显式白名单说明哪些只是引用了发送按钮或不产生对外消息（找按钮、拼音候选、OCR 匹配、回车开会话）。把这条 gate 删掉，自检立刻 3 项红并点名 `moment.py:MomentCommentDialog.send`。其余写入口已逐个跟到底：`quick_send`→`send_msg`→`click_send`、`quick_quote`/`at_member`→`click_send`、`send_text_to`→`send_text`、`ReplyCommentMoment`→`ReplyComment`→`_click_comment_send`、`wx.SendMsg`→`send_msg`、`sender.send_to`→`send`，全部落在已有的 gate 之后。
- **实机复验（2026-09-22，真实客户端，`calm` 档，逐项拉开间隔）**：向文件传输助手发送并逐字回读通过（库里新 `sort_seq` 精确命中，节流台账记下一次写动作，混合路径日志显示 UIA 驱动已启用）；`back_to_chat_tab()` + `open_chat('文件传输助手')` 端到端通过。
- **已知问题（本轮未修，并更正 1.2.2.6 的说法）**：1.2.2.6 写的「临时清掉 `WS_EX_TRANSPARENT` 之后 UIA 点击生效」在本机**没有复现出效果**。实测：样式确实被清掉了（`ex=0x80000`），但 `WindowFromPoint` 在导航栏那一点仍然返回主窗口 `Qt51514QWindowIcon` 而不是渲染子窗，点击之后页面纹丝不动（整窗像素变化 0.0%）；`Control.Click()` 与 `Invoke()` 同样无效。而把该样式跨进程长期摘掉时，同一个点又能切页（15.3%）——所以真正的命中测试判据不止 `WS_EX_TRANSPARENT` 一位（`MMUIRenderSubWindowHW` 是分层窗口，很可能还有逐像素 alpha 在起作用）。基于搜索框 / `ValuePattern` 的路径不受影响，因此发送、搜索、打开会话都正常；**导航栏的坐标点击待查**，本轮没有改这段代码，也没有把「已修复」这句话留在文档里。
- **回归覆盖**：`tools/selftest.py` 新增 `gate` 组 35 项（合成 PE 片段 + 临时缓存文件 + 假窗口句柄，纯离线不碰微信），`rhythm` 组扩到 42 项（含按动作的节流审计），整套 131 项。三处变异验证：删掉缺 numpy 的回退分支 → 该组崩溃；去掉位移的符号扩展 → 6 项失败；删掉评论节流 → 3 项红并点名函数。

### v1.2.2.6（2026-09-21）

- **修复：`calibrate_layout()` 在已发布的 1.2.2.5 里必抛 `NameError`**。`guia.py` 的超时包装 `_run_with_timeout` 用了 `threading.Thread`，模块顶部却没有 `import threading`（对照 1.2.2.5 的 wheel 实测：`threading.Thread` 在、`import threading` 不在）。布局校准是 UIA 路线的入口路径，pip 安装的用户第一次校准就会撞上；本机开发副本因为单独同步过，所以看不出来。**两档的症状并不相同**（这也是为什么必须报两次）：`wide` 下校准直接返回 `False` 且不生成布局文件，`portrait` 下发送按钮探针把同一个异常吞掉，照样返回 `True` 并用默认比例。把已同步副本里的模块属性删掉后，两种症状都复现了。现在探针内部出错会留一行日志，外层 `except` 把「代码缺陷」（`NameError`/`UnboundLocalError`/`AttributeError`/`TypeError`/`ImportError` → `wxlog.error`，控制台可见）和「这次没测出来」（OCR 没认到锚点 → debug，按设计回落默认比例）分开——补上 import 并不保证 OCR 能认到锚点，那是回落而不是失败。
- **修复：发送回读校验可以确认一条其实没发出去的消息**（`_verify_sent`）。两个独立缺口：一是**子串匹配**，聊天输入框里留着草稿时实际发出去的正文是 `校准wechatauto 部署自检 OK`，而调用参数是 `wechatauto 部署自检 OK`，库里查得到、内容却是错的；二是**没有水位**，只要最近几条里存在一条含目标文字的旧消息就能通过——UI 返回成功只会转入轮询、不会重发，所以旧消息在第一次检查就被接受。现在普通文本要求**逐字相等**（`strip()` 不等于逐字一致），引用/回复/@ 保留包含匹配（微信会把正文包装，只能包含匹配），并且每个调用点都显式写明口径；每次校验发送前先给目标会话拍一个**落库水位**：最大 `sort_seq` **加上**顶部若干行的 `(sort_seq, local_id)` 身份集合——真实 `sort_seq` 大量并列（同会话实测最多 8 行同值），只比 `>` 会把刚发出去那条判成旧消息。回读还会先把显示名解析成 `username`：消息表按 `username` 键，名字错了**静默返回空**，群聊的校验此前是以错误的理由失败。拍不到水位（新会话、DB 不可用）时退回不带水位的检查，宁可不加门槛也不误报失败。
- **回归覆盖**：`tools/selftest.py` 新增离线 `verify` 组（假 DB，14 项）和离线两档 `calibrate_layout` 组（假窗口，含锚点命中路径，8 项），离线合计 36 项通过、整套 gate 55 通过 0 失败；校验口径另外在**真实解密库上只读**复跑过（未发送任何消息），端到端实机发送镜头仍待补。
- **修复：UIA 的点击会落到微信背后那个窗口上**。微信内容窗口 `MMUIRenderSubWindowHW` 带 `WS_EX_TRANSPARENT`（实测 `exStyle=00080020`），裸 `mouse_event` 在该坐标会被命中测试跳过、投递到它后面那个普通 `Qt51514QWindowIcon` 主窗口——4.1.13 之后「点了没反应」就是这里来的。`uia_driver` 现在在事件前后临时清掉这个扩展样式并立刻还原（`_click_at` / `_click_ctrl`），与 `guia.wx_click` 早前的做法一致。滚轮是例外：它在那层窗口上本来就有效，未改动。
- **修复：微信停在朋友圈页时 `open_chat` 无法自行回到聊天页**。那一页没有会话列表，基于搜索的打开路径永远不会确认成功——实测两种形状：高 CPU 空转 8 分钟以上，以及约 90 秒后放弃、返回 `None` 且屏幕上看不出任何原因（整掉了一镜录制）。新增 `WeChatUIA.back_to_chat_tab()`，点 `mmui::MainTabBar` 里的「微信」。它**不判断当前在哪一页**：该 build 的 `XTabBarItem` 完全不暴露选中态（`ButtonControl`、无 `SelectionItem` 模式、`LegacyIAccessible.State` 恒为 0），而且切页后各页控件仍常驻树中，所以只能无条件点一次——已经选中时再点只会把会话列表滚回顶部。
- **修复：搜索框校准比例会把会话名粘进当前打开会话的输入框**。旧的 `SEARCH_BOX_RATIO` 算出的点击中心已经偏出真实搜索框（算得 212,120，实测框在 250,152–412,192），于是粘贴落到了上一个会话的输入区。`_search_chat` 现在优先取 UIA 的 `search_box_rect()`，粘完还会自查：文字若进了聊天输入框，就用 `ValuePattern.SetValue('')` 清空并放弃搜索兜底——不会把任何东西发出去。
- **UIA 驱动新增**：`search_box_rect()`、`_set_text()`。`ValuePattern.SetValue` 不需要按键也不动剪贴板就能触发微信的实时搜索，但它**不会**让 Qt 控件获得焦点，所以只用于搜索框；发送那一路仍然必须依赖聚焦后的输入框和 `{Enter}`。
- **隐私修复**：`media.py` 不再往 stdout 打 12 条裸 `[DBG]`，其中一条带着含账号 **wxid** 的 `.dat` 绝对路径。现在统一走 `wxlog.debug`（控制台默认 INFO），另有两处静默 `return None` 的出口改成 warning。
- **本轮未做实机复验**：`_click_ctrl` 与 `back_to_chat_tab` 的点击是否真的落到微信内容区（重登录打断了这一步）。`WS_EX_TRANSPARENT` 的测量和搜索框矩形是实测的，两条新点击路径的端到端效果尚未演示。
- **感谢 [wenjiavv](https://github.com/wenjiavv)** 报告上述两条（[#28](https://github.com/fanyuantaier/wechatauto-replica/issues/28)、[#29](https://github.com/fanyuantaier/wechatauto-replica/issues/29)），并给出了复现、宽屏/竖屏症状区分和修复建议。
- **修复：`RecallGuard` 一条撤回都抓不到 —— 两层独立故障**。
  1. **撤回行永远不会被投递**。微信撤回是**原地改写**原文那一行（本机 208 个会话里的 64 条 `revokemsg`：`revoketime - create_time` 落在 1-30s 的 10 条、31-300s 的 54 条、等于 0 的 0 条），改写后 `local_id` 和 `create_time` 都还是原文的；而 `Listener` 的增量判据是 `sort_seq > watermark`，`sort_seq` 没变，撤回事件根本不下发。现在 `watch()` 自带一条 `wxrecall-scan` 守护线程，每 `scan_interval`（默认 2.0s）重读各会话最近 `scan_limit`（默认 30）条，拿 `local_id` 跟镜像比对：镜像里是正常消息、现库已是 `revokemsg`，即一次撤回；另有 `scan_now()` 供脚本手动触发。
  2. **就算投递了也查不到原文**。`_find_original` 的条件是 `create_time < revoke_time`，而 `revoke_time` 取的正是这条撤回行自己的 `create_time`（= 原文时间），严格小于把自己排除了。现在先按 `(chat, local_id)` 精确取（被改写的就是这一行，同 `local_id` 的镜像行必然是原文），时间窗只作兜底且放宽成 `<=`。
  3. `on_msg` 遇到撤回行**不再写镜像**，否则一存就把要救的原文覆盖掉。
  4. 撤回时刻改为解析 `<revoketime>`（原来打出来的是原文的发送时间）；`(chat, revoke_time)` 同时作去重键，Listener 路径与轮询路径不会各记一笔，重启也不会把历史撤回再报一遍。
  5. `close()` 现在会停掉轮询线程。
  实测：离线 3/3 条真实撤回行（local_id 62/64/88）还原出原文，二次 `scan_now()` 返回 0 条（去重生效），`close()` 后只剩 `MainThread`；活体链路（发送 → 镜像 20→21 条 → 撤回 → 还原）同样跑通。
- **安全修复：`demo_media.py --list` 会把媒体消息的 XML 原样打到屏上**，其中含 `aeskey`、`cdnthumbaeskey`、`cdnthumburl`、`md5` 等密钥与 CDN 取回字段。现在只输出尺寸、字节数、时长和文件名。
- **新增：微信 4.1.13 合并布局下朋友圈点赞/评论的 OCR 兜底**（`Moment._read_comment_cell_ocr`）。该版本的点赞与评论落在兄弟 cell `mmui::TimelineCommentCell` 且不进 UIA 树，正文 cell 的 UIA 解析必然为空；现在检测到空结果时自动兜底为「评论区矩形截图 + OCR」。同时把评论框的检索区按新布局改为从视口底边往上 320px（旧代码找 `bottom+8` 以下，在 4.1.13 上落到任务栏、永远匹配不到）。
- **清理**：`demo_send.py` 的 `pick_default_image` docstring 改为 raw string，消除 `\W` 转义告警。

### v1.2.2.5（2026-09-19）

- **修复：朋友圈缓存图片解密后解不开**。同一条 `.dat` v2 路径上的两个独立问题：
  1. **缓存容器用错了单字节 XOR 密钥**。这个密钥就是账号配置 dword 的低字节，但原实现是**逐文件从明文最后两字节反推**（`tail ^ 0xFF == FF D9`）。微信会在 Sns 缓存容器的图片结束标记**之后再追加 24 字节页脚**（本机实测 189/295），判据因此失效并静默退回到兜底密钥，尾部一段全成乱码。现在按 **配置 dword（权威）→ 缩略图统计 → 兜底** 的顺序解析，并按账号缓存一次。
  2. **页脚被当成图片数据留下**。解密后按 JPEG/PNG 结束标记裁剪，严格解码器不再因为几个尾巴字节判整张图无效。
  实测：Sns 缓存容器过 `MediaDownloader.decrypt_image()` 从 **118/295 → 295/295**；朋友圈缓存索引的解密失败 **177 → 0**；15577 个聊天图片抽样 **429 张 JPEG + 171 个 wxgf、0 失败**（聊天媒体无回归，wxgf/WXAM 容器不受影响）。
- **修复：`MomentDB.find_local_media` 的尺寸校验在最常见分支上根本没跑**。「尺寸偏差过大即拒绝冒充」只写在**多个**同尺寸候选那一路；只有 1 个候选时代码直接返回、完全没比对，所以 66KB 的偏差静默放行。现在会记录偏差，但**仍然不否决**：库里声明的 `totalSize` 是 CDN 原图尺寸、缓存里是微信重编码后的副本，偏差大是常态，不能当「认错图」的证据（多候选那一路的判据保持不变）。

### v1.2.2.4（2026-09-18）

- **修复：密钥形式错误会让整个消息分片读不出来**。密钥缓存里可能存成 48 字节（32B key + 16B 显式 salt）形式，而解密按**密钥长度**选分支——48 字节会走「明文头库」分支，解出的文件头不是 SQLite（`file is not a database`），该分片（实测 96MB 的 `message_0.db`）整库不可读、读消息全部失败。现三重防护：**存储时先按标准形式校验**（通过即存 32 字节裸密钥，仅明文头库才存 48 字节）、**读取时归一化判定**、**缓存加载时自动纠正历史条目**。
- **修复：微信持续写入时「数据库合并失败」直接报错**。原实现把解密结果直接写在缓存文件上，合并失败即抛 `RuntimeError`，且会毁掉上一份可用副本。现在：先做**主库自洽快照**当底线（解密后 `quick_check`，失败自动重读最多 4 次）→ 再在副本上尝试合并 WAL（失败降级为「仅主库快照」并告警）→ 全程写临时文件、**成功才原子替换**，任何失败都不破坏上一份可用副本。
- **修复：缓存残留已不存在的库条目会让构造整体崩**（`KeyError`）——现已整体容错。
- **布局：新增手机式竖屏（双档位 `wide` / `portrait`）**，按窗口长宽比自动选档、两档独立校准并存于布局文件（旧格式自动迁移）；同时修复**竖屏下会话定位失效**（名字列过滤把整列会话名当成头像区滤掉 → `find_session` 恒返回 None）。
- **清理**：删除 10 处未使用 import；8 处「吞异常」补 debug 日志（不再把探测失败伪装成正常结果）；`demo_send.py` 去掉他人用户名与本机路径、默认图片改为自动探测 RWTemp；README 中真实 wxid 改占位。
- **新增 `tools/selftest.py`**：只读自检（layout / keys / sessions / messages），`python tools/selftest.py` 一次跑完。

### v1.2.2.3（2026-09-16）

- **修复：库运行期间磁盘持续约 50MB/s 读写**。解密缓存的 stamp 判定用**精确浮点相等**比较 mtime，而写盘用 `%f`（只 6 位小数）、Windows 的 mtime 有 7 位小数 → 每轮查询（约 1s）都被判定为“库已变化”，于是反复全量解密 + 合并 WAL + 重写缓存。现改为 `STAMP_VERSION 3` + `%r`（完整往返精度），升级后重建一次即稳定。
- **消息读取改为“分片内先 LIMIT 再合并”**（48k 条大群实测 **5.5×**：1.053s → 0.191s；`get_new_messages` 约 6×）。超大群不再把每个分片的全部行物化进 Python；`get_messages` / `get_new_messages` / `get_message_row(..., local_type=)` / `get_message_rows_for_media` 等**公开 API 签名与结果均不变**（6 个会话 × 71 个用例逐条比对一致）。
- **修复 UTF-8 模式下“取不到密钥”**：4 处 `tasklist` 调用未指定编码，在 `python -X utf8` / `PYTHONUTF8=1` 下解码 GBK 输出失败 → `stdout` 变成 None → `AttributeError`，整个取密钥流程崩掉。现统一 `encoding="gbk", errors="replace"` 并对 stdout 兜底。
- **`WeChatUIA.is_running()` 改为多判据**：原来单判据 + `except → False`，任何异常都会变成“微信未运行”的**假报错**；现为 tasklist / 主窗口标题 / psutil 三判据，且只有所有判据都**出错失败**时才写 stderr 说明原因。
- **演示脚本与文档中的真实联系人/群名清理**：统一改为「文件传输助手」（示例默认值「兔仔仔」「送你挖银子」保留）。

### v1.2.2.2（2026-09-13）

- **密钥问题根除（不再“每次微信更新都复发”）**：三层修复。
  - **缓存不再被写空**：`_save_keys()` 空结果不落盘（原子写 + 保留 `.bak`）。此前提取偶发失败（选错账号/权限）会把**好缓存覆盖成空文件**，之后每次启动都报“0 把密钥”——故障现场的 `keys cached: 0` 即由此而来。
  - **稳定密钥副本**：自动在 `%LOCALAPPDATA%\wechatauto_keys\<账号>.json` 保留一份（可用环境变量 `WECHATAUTO_KEYS_DIR` 指定目录，例如项目工作区），跨 TEMP 清理与微信更新复用。启动时按「稳定副本 → 工作缓存 → `.bak` → 其它账号缓存」**多位置合并**，并逐条页1 HMAC 校验，只保留真能用的。
  - **账号选择改由密钥校验决定**：不再按“最近修改的 .db”猜账号（微信每次更新会重写 .db，mtime 全变 → 选错账号 → 0 密钥）。现在一次内存扫描收集候选密钥，对**每个账号目录**分别做页1 HMAC 打分，选能解开的那一个并自动切换（日志：`已按密钥校验选定账号目录: …`）。
- **cfg 主密钥告警**：cfg 路径在微信 4.1.13+ 会返回**不可信的主密钥**（v1.1.9 起已降级为回退路径）；现在它复现不出任何库密钥时会**明确告警**，不再静默当作成功。
- **诊断增强（`diagnose_keys`）**：新增微信客户端 **FileVersion**、逐账号「缓存可用 / 主密钥派生」计数、**主密钥一致性检查**（判断“密钥属于哪个账号”）；`_open` 报错文本直接列出三条经典原因（32 位 Python / 权限与微信不一致 / 多账号选错）+ `account=` 提示。

### v1.2.2.1（2026-09-12）

- **兼容微信新版界面（4.1.13.65 实测）**：新版把 `AutomationId` 从短名改成了**点分路径**（旧 `session_list` / `chat_input_field` → 新 `MainView.main_tabbar`、`MainView….main_window_sub_splitter_view…`），原来按短名精确等值匹配会失配。现在 AID 一律按「精确 / 点分段相等 / 结尾匹配」判定（`_aid_hit()`），旧版短名与新版路径都能命中。
- **窗口标题匹配放宽**：新版主窗口标题为 `Weixin`、带未读数时变成 `微信(3)`；`_title_is_main()` 改为包含匹配，`WeChat` 等其它窗口不会误匹配。
- **锚点候选 + 结构兜底**：主窗口 / 登录窗 / 搜索框改为候选元组匹配（单值常量保留，兼容外部引用）；搜索框、聊天输入框、搜索结果列表各自增加结构兜底（Name 含「搜索」的 EditControl / 聊天区 EditControl / 根节点属性搜索），日后新版改类名或 AID 时不至于整体失效。
- **新增布局自检 `WeChatUIA.describe_layout()`**：一次调用返回主类名、标题、布局类型（`merged` 合并布局 / `legacy` 独立朋友圈窗 / `chat`）与各锚点解析结果（main_window / search_box / session_list / chat_input / main_tabbar / sns_list）。微信再改界面时先跑它，即可定位是哪个锚点失配。
- 说明：朋友圈相关锚点本身已是双布局分支（独立 `mmui::SNSWindow` / 合并 `mmui::SNSContentView`），4.1.13.65 实测类名未变，无需调整。

### v1.2.2（2026-09-12）

- **修复跨分片消息读取（消息/语音不全）**：同一会话的 `Msg_<md5>` 表实际横跨多个 `message_*.db` 分片，而 `get_messages` 只命中第一个分片——实测某会话真实 8904 条消息（24 条语音）此前只返回 1 条。新增 `_find_msg_tables()` / `_msg_conns()` / `_shard_rows()`，跨全部分片合并后按 `sort_seq` 排序；`get_messages`、`get_new_messages`、`_find_media_rows` 均改用合并视图。`get_message_row` 新增 `local_type` 过滤（`local_id` 跨分片**不唯一**），并新增 `get_message_rows_for_media()` 返回全部分片命中行；媒体下载各方法传入类型码以选中正确分片行。
- **监听可靠投递（行为变更）**：水位改为**回调成功后才推进**（新增 `_inflight` 分派边界，回调未确认前不重复分派同一消息），回调失败按 `max_retries`（默认 3 次）重试后再记丢弃；水位自动落盘 `listener_watermark.json`。进程停机期间到达的消息会在下次启动时补投，不再被静默跳过。传 `watermark_file=""` 可关闭落盘。
- **文本还原不再要求含中文**：纯英文 / 纯数字 / URL / Emoji 的容器格式消息不再退化成 `[文本]`（改为可打印率 + 字符类别双重判定）。
- **`Chat.GetNewMessage()` 不再丢积压**：单批 200 条以上时连续分批拉取直到追平，水位只推进到**实际取回**的最后一条，不再直接跳到库内最新位置。
- **UIA 物化自愈（微信重启/升级后子控件全扫不到）**：微信重启或升级后 Qt accessibility gate 字节归零，`mmui::` 树退化为 Qt 空壳（`Qt51514QWindowIcon` + 2 个节点，扫不到任何控件）。现在会热写 gate → **校验 `mmui::` 是否真的出现** → 失败自动换候选 RVA 重试（真正生效过的 RVA 按 DLL 身份缓存）；`_get_uia()` 增加 30s 节流自愈，不再「一次唤醒失败就永久降级 OCR」，也不再需要人工 `refresh=True`。兜底表补 `4.1.13.65 → 0x0AE2B0C8`。
- **朋友圈滚动定位修复**：加入反向上限（每轮最多反向 1 次，之后单向向下）与卡死检测（顶部指纹改为含包围盒几何——合并布局整屏复用 ListItem、同名 cell 不再误判「卡死」而中途放弃）；DB 标尺判断目标在下方时**跳过「先滚到顶部」**；停止判据改为**下一条朋友圈 UIA 出现即停**；方向与距离修正（被裁像素换算滚轮格数）+ 底部余量，解决「翻的距离不够、够不到 … 按钮」。
- **消息类型表**：支持微信 4.x 复合 `local_type`（按低 32 位分解真实类型）；新增 `50 音视频通话`（`<voipmsg>` 气泡）、`11000 动画表情`、`8594229559345 红包`（库侧此前被低 8 位映射误标为「文件/链接/卡片」）；空正文（表情/贴纸类）显示 `[动画表情]` 占位；`demo_group_messages` 对所有类型统一 zstd 解压并输出一行摘要。
- **`demo_listen.py --all`** 自动发现新会话（此前只取启动时最近 30 个，之后新建会话不会加入监听）。
- **新增防撤回监听 `RecallGuard`（测试版）**：`watch(listener)` 后把每条新消息写入独立镜像 sqlite 库、附件（图片/语音/视频/文件）增量备份到 `media/`；收到 `revokemsg` 系统消息时终端打印 `[撤回] 撤回者 → 原文` 并写入 `recall_events` 表。**未充分实机验证，按测试版发布。**
- **新增 `MomentObserver`（测试版）**：朋友圈缓存 key 的「观察即固化」——`snapshot()` / `diff()` 快照与轮询导出（缓存 key 与 feed md5 之间无可推导映射且缓存易失，故观察即可固化）。**未充分实机验证，按测试版发布。**

### v1.2.1（2026-09-06）

- **新增「引用消息并发送」（测试版）**：`WeChatGUI.quote_msg(text, who, target_text=None, verify=False)` 右键定位消息 → 弹出菜单选择「引用」→ 输入内容并发送；`target_text` 省略时引用最近一条。`quick_quote()` 提供一行式入口，示例脚本 `wechatauto/demo_quote.py`。
  - **测试版说明**：引用功能走「坐标 + OCR + SendInput」模拟点击路线，依赖微信 4.1.x 自绘渲染布局；随窗口尺寸/DPI/会话内容不同可能存在定位偏差。右键采用 `SendInput` 注入（微信渲染窗口对 `mouse_event` 右键不响应），光标先 `SetCursorPos` 移至目标再注入，避免“只移动不点击 / 只点击不移动”的错位。使用中发现定位不准时请调整会话内消息布局后重试。
- **移除 `desktop_available()` 桌面白屏判定**：控件定位已全面走 UIA，不再依赖整窗截图白色占比采样——该判定在微信窗口正常时曾误报「窗口不可见」。`ensure_visible()` 现以窗口句柄存活判定可见性，保留「最小化遮挡窗口 + 置顶微信」的前置动作。

### v1.2.0.3（2026-08-31）

- **修复 WAL 合并后数据库解密缓存损坏导致死循环**：`_check_merged` 之前用 `SELECT count(*) FROM sqlite_master` 只查 schema 树，数据页损坏仍能通过校验，缓存 stamp 标记为"最新"后每秒轮询复用坏缓存，反复抛 `database disk image is malformed` 形成死循环。改用 `PRAGMA quick_check` 全库校验（含数据页/索引页）；新增 `_invalidate_cache()` 清空解密 `.db`/`.stamp` 缓存；查询统一入口 `_run_msg_query`：命中 malformed 时清缓存→重建→自动重试一次；`_msg_conn` 及时关闭分片库连接避免 Windows 文件占用。

### v1.2.0（2026-08-30）

> 注：本版本合并了 1.1.10.2 之后、此前尚未发布的全部改动（1.1.10.3 → 1.1.10.7 的内容）。

- **朋友圈智能定位与自动点赞**：`Moment.find_moment(publisher, keyword, ...)` 采用 **数据库路线计算目标偏移 + UIA 路线滚动定位** 的混合方案——先用本地 `sns.db` 标尺算出目标动态相对当前可见条目的索引偏移，再按偏移方向动态滚动（自适应步长），最终定位到指定作者/关键词的朋友圈，摆脱了“盲目往下翻”和“过早判定未找到”的问题。
- **“…”浮层识别**：`Moment._locate_more_click` / `_find_more_button` 通过模板匹配（深浅两套模板，随包打包进 `assets/`）定位朋友圈右下角“…”按钮并点击，未识别到时自动微调滚动重试，弹出点赞/评论浮层。
- **一键点赞**：`Moment.LikeMoment(publisher, keyword, ...)` 一键完成“定位 → 点…→ 浮层内点赞”；浮层内“赞/评论”按钮通过从 UIA 根节点向下做全局深度遍历按名称匹配后按其中心坐标点击。
- **朋友圈点赞/评论（UIA 控件路线）**：`WeChat` 现暴露 `Moment` 属性与 `SwitchToMoments()`，通过热激活 `mmui` UIA 树并点击导航栏“朋友圈”。`Moment.Like(item, cancel=False)` 与 `Moment.Comment(item, content, reply_to=None)` 基于 UIA 控件对动态条目操作——点赞/评论属服务端行为，只能走界面（数据库路线保持只读）。UIA 树不可用时 `WeChat.Moment` 为 `None`。示例 `wechatauto/demo_moments_interact.py`。
- **朋友圈图片/视频下载**：新增 `MomentDB.download_media(media, save_dir, kind)`——优先从本地缓存原样复制（离线、秒级），缓存缺失时回退到 CDN url 下载；`MomentDB.download_moment_media(feed, save_dir, ...)` 批量把一条动态的图片/视频落地到目录。`find_local_media(md5, kind, size)` 按 md5 定位缓存文件，对视频按 `totalSize` 跨整个 `Sns/Video` 树按大小近似匹配（视频缓存文件名是内容哈希、与朋友圈记录里的 md5 无关，故用大小找回真实 MP4）。`parse_feed` 现通过 `videomd5` / `videoDuration` / `type` 区分图片与视频，并记录每条媒体的 `size`。示例 `wechatauto/demo_moments_download.py`。
- **朋友圈读取 API（数据库路线）**：`MomentDB.get_moments()` 新增 `since` / `until`（Unix 秒时间过滤）与 `keyword`（正文过滤），并支持 `limit=0` 全量返回。新增增量同步 `latest_tid()` / `get_moments_since()`，便于轮询检测「有新朋友圈」。新增互动通知 `get_interactions()` / `interactions_unread_count()`，读取「他人对我朋友圈的赞/评论」表（`SnsMessage_tmp3`）。新增 `comment_tree()` / `comment_reply_to()`，按 `comment_id` / `ref_comment_id` 将评论组织成回复树。
- **新增群名 ↔ 群ID 互查**：`get_groups()` 现在返回每个群的真实 `name`（来自 contact 表，无群名时回退 wxid）。新增 `group_name_to_id(name)`（先精确匹配，再子串/模糊匹配）与 `group_id_to_name(chatroom_wxid)`，可按群显示名反查群 wxid（及反向），便于与 `get_group_members()`、`at_member()` 配合使用。
- **新增群成员枚举与变动监测（只读，无需 UI）**：新增 `WeChatDB.get_groups()` / `get_group_members(chatroom_wxid)`，读取 `contact.db` 的 `chat_room` + `chatroom_member` + `contact` 三表关联，返回每个群的成员。新增 `GroupMemberWatcher`（经 `get_group_member_watcher` 创建）：先 `capture()` 存基线快照，之后 `poll()` 对比当前成员输出 `joined` / `left` 差异，实现轮询式群成员变动监测。可与现有 UI 自动化的 `at_member()` 配合使用。
- 新增可运行示例 `wechatauto/demo_moment_find.py`、`demo_moment_more.py`、`demo_moment_like.py`；新增依赖 `pyautogui`、`opencv-python`。

### v1.1.10.2（2026-08-30）

- **修复全新安装后长文本仍显示 `[文本]`：新增必需依赖 `zstandard`**。微信4.x 将长文本的 `message_content` 存为 zstd 压缩帧，由 `_friendly_content` 通过 `import zstandard` 解压。但 `zstandard` 此前不在必需依赖中，用户机器未安装时该 import 被静默吞掉，长文本退化为 `[文本]` 占位符（监听本身正常，故难定位）。现已将 `zstandard` 加入必需依赖；`_friendly_content` 同时新增惰性双包名导入（`zstandard`/`zstd`，见 `_get_zstd_module()` / `_zstd_decompress()`）。

### v1.1.10.1（2026-08-29）

- **修复消息读取的 `AttributeError: 'sqlite3.Row' object has no attribute 'get'`**：`_msg_row_to_dict` 对 `sqlite3.Row` 调用了 `.get("compress_content")`，而该对象只支持下标 `[]` 访问。当消息内容解压后仍为占位符（如表情等特殊类型）时走此分支，导致实时 `Listener` 轮询循环崩溃。现改为下标访问并容错，`get_messages` / `get_new_messages` / `get_message_row` 均修复。

### v1.1.10（2026-08-27）

- **新增原图下载功能**：`MediaDownloader.download_image_original()` 通过UI自动化点击图片消息，触发微信下载原图。解决了群聊图片只有缩略图可用的限制。
- **修复长文本消息内容提取**：添加zstd解压支持、`compress_content`回退，修复换行符处理问题。

### v1.1.9（2026-08-27）

- **修复微信4.1.13+密钥提取**：调整密钥提取优先级，将`Config.Cipher`内存扫描置于`extract_master_key_from_cfg`之前。cfg提取方式在微信4.1.13.12上返回错误的主密钥，而Config.Cipher扫描（从XOR解码的blob中读取原始`enc_key`值）工作正常。此修复解决了新版微信"0/24密钥验证通过"的问题。

### v1.1.8（2026-08-25）

- **修复 MediaDownloader 缺失 `_derive_xor_key` 方法**：v1.1.7 发布时意外遗漏了 `_derive_xor_key()` 方法，但代码路径（`_decrypt_v2`、`detect_image_key`）仍引用它，导致图片解密时出现 `AttributeError`。已恢复该方法，用于从缩略图 `_t.dat` / `_h.dat` 文件反推 XOR 密钥。
- **修复群聊 `sender_id` → `sender_username` 映射**：`Listener` 回调现在会在消息字典中返回 `sender_username`（wxid 格式），通过 `message_resource.SenderName2Id` 映射表将数字 `sender_id` 转换为可直接用于 `search_contact()` 的用户名。
- **感谢 [uiharukazari0105](https://github.com/uiharukazari0105)** 报告 v1.1.7 版本缺失 `_derive_xor_key` 方法的 bug。

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
  微信号再搜索（`_resolve_search_keyword`），并清空搜索框残留重试；
  实测 `open_chat('wxid_xxxxxxxxxxxx')` 成功。
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
  - 实测：`send_msg('文件传输助手')` 10.2s、`send_msg('某好友')` 13.2s
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
    预览行（如「00，包含：某好友」）与群名结尾行，只点联系人，修复
    「搜索选中群聊而非联系人」的问题；
  - `_chat_open_confirmed` 改为**优先标题命中**，标题读不到才退而用面板
    非空白作为已打开判据，修复「点错会话也误判成功」；
  - `open_chat` 首查 `_chat_is_open && _pane_has_content`，右侧面板已打开
    目标会话时直接成功（不再滚动/搜索），已打开场景耗时 45s → 2.7s。
- **OCR 多轮投票**（`find_session._scan_vote`）：WinRT OCR 对生僻字存在
  抖动（同一行不同轮次可能读出「某好友」或「亠人五」）。对侧栏放大 3x
  扫描 4 轮，命中行按 y 聚类（≤30px 视为同行），票数 ≥2 才返回，显著
  降低误配；普通会话仍走单轮快速路径，无性能损失。
- 实测：`find_session('某好友')` 连续 5 轮 4/4 票一致、稳定命中；
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
3. **发送为 GUI 操作**：锁屏/会话断开时窗口不响应，发送接口返回明确失败；
   文件/图片/回复/艾特代码已完成但需桌面解锁后实测；
4. **视频文件未落盘时不可下载**：视频 mp4 仅在本地存在（`msg/video`）时
   返回，否则返回 None；
5. **发朋友圈功能已舍弃**：4.x 的发表为自绘界面操作，不可靠自动化；
   本库仅保留朋友圈读取/点赞/评论能力。
6. **评论/回复功能仅供测试**：`回复某条评论`（`ReplyComment`）通过截图
   OCR 定位评论区评论行，再驱动界面点击/粘贴/发送；朋友圈评论区为自绘、
   布局多变，稳定性无法保证，仅建议在测试账号中验证流程，勿用于生产。
7. **引用消息功能（BETA）仅供测试**：`quote_msg` 通过坐标 + OCR + SendInput
   模拟右键菜单选择「引用」，依赖微信 4.1.x 自绘渲染布局，随窗口尺寸/DPI/
   会话内容不同可能存在定位偏差，仅建议在测试账号中验证流程。

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

---

## 🇬🇧 English

### wechatauto-replica — WeChat 4.x Windows Automation (wxauto-compatible)

![PyPI version](https://img.shields.io/pypi/v/wechatauto-replica)
![PyPI downloads](https://img.shields.io/pypi/dw/wechatauto-replica)
![Python](https://img.shields.io/pypi/pyversions/wechatauto-replica)
![License](https://img.shields.io/github/license/fanyuantaier/wechatauto-replica)
![GitHub stars](https://img.shields.io/github/stars/fanyuantaier/wechatauto-replica)

Automate the **WeChat 4.x Windows desktop client** (not the web version): read messages, listen in real time, download media, export full history, read Moments (朋友圈), and send messages — by driving the local client directly.

> **Current version:** 1.2.4 · Windows 10/11 · Python 3.9+ (verified on 3.12) · WeChat **4.1.12+** (verified on 4.1.15.13)
>
> **Why this project exists:** the classic [wxauto](https://github.com/cluic/wxauto) relies on the UI Automation tree, which WeChat 4.x broke with self-drawn rendering (no accessibility nodes). wechatauto-replica is a drop-in-style replacement: messages are read through **local database decryption** (SQLCipher 4), and sending uses a **UIA + OCR hybrid** driver that auto-falls back between engines.

![Reading encrypted WeChat 4.x databases](docs/demo_db_files.gif)

*Reading the encrypted `contact.db` / `message_*.db` / `sns.db` files directly from `xwechat_files/.../db_storage/` — no web API, all local.*

## ✨ Features

| Capability | Status | How |
|---|---|---|
| Read messages | ✅ verified | Local SQLCipher 4 DB decryption (`wechatauto/db.py`) |
| Real-time message listening | ✅ verified | `Listener` incremental polling, per-chat worker threads |
| Emoji message capture | ✅ verified | Screen capture + direction-aware bubble auto-cropping |
| Full history export | ✅ verified | JSON / SQLite |
| Media download (image / voice / file) | ✅ verified | `MediaDownloader`: image v2 AES decryption, SILK voice, files |
| Moments (朋友圈) read | ✅ verified | Direct `sns.db` reads (3382 feeds verified) |
| Multi-account | ✅ verified | `list_accounts()` + `account=` |
| Send text / file / image / reply / @member | ✅ verified | UIA-first, coordinate + OCR fallback |
| Voice call / Poke (拍一拍) | ✅ verified | UIA buttons + OCR menus |
| UIAutomation tree | ✅ after hot-activation | Writes the Qt accessibility gate inside Weixin.dll |

## 🚀 Quick Start

```bash
pip install -e .
# extra deps for the OCR sending path:
pip install winsdk pypinyin
```

### Read messages

```python
from wechatauto import WeChatDB

db = WeChatDB()  # auto-detects account & data dir (WeChat must be logged in)

info = db.get_self_info()                    # current account
for s in db.get_sessions(limit=10):          # session list
    print(db.get_nickname(s["username"]), s["unread"])

hits = db.search_contact("Ayi")              # search contacts
for m in db.get_messages("filehelper", limit=10):   # recent messages
    print(m["create_time"], m["sender_id"], m["type"], m["content"])
```

### Send a message

```python
from wechatauto.guia import quick_send, quick_send_file

quick_send("Hello", "filehelper", verify=True)   # verify=True reads back from DB
quick_send_file(r"D:\report.pdf", "filehelper")
```

### Real-time listening

```python
from wechatauto import WeChatDB
from wechatauto.db import Listener

db = WeChatDB()
lst = Listener(db, interval=1.0)
lst.add_listener("filehelper", lambda msg, lst: print("new:", msg["content"]))
lst.start()
# ... your code ...
lst.stop()
```

Callbacks run on dedicated per-chat worker threads: messages in one chat are processed in order, different chats in parallel; slow callbacks (AI calls, image recognition) never block the poller.

### Media & Moments

```python
from wechatauto import WeChatDB, MediaDownloader, MomentDB

db = WeChatDB()
md = MediaDownloader(db)
md.detect_image_key()          # scan process memory for the image AES key (persisted after first hit)
for m in db.get_messages("filehelper", limit=50):
    out = md.download_media("filehelper", m["local_id"])
    if out:
        print("downloaded:", out)

moments = MomentDB(db)
for feed in moments.get_moments(limit=10):
    print(feed["nickname"], feed["text"])
    print("  images:", [i["md5"] for i in feed["images"]])
    print("  likes:", [l["nickname"] for l in feed["likes"]])
    print("  comments:", [(c["nickname"], c["content"]) for c in feed["comments"]])
    # download this feed's pictures & videos (local cache first, then CDN url)
    saved = moments.download_moment_media(feed, save_dir=r"D:\moments")
    print("  saved:", saved)
```

See `wechatauto/demo_moments_download.py` for a runnable download demo
(`python -m wechatauto.demo_moments_download [N] --out 目录`).

**Like & comment** are server-side actions done through the client UI, so they
use the UIA-tree route (not the local DB) — `WeChat` hot-activates the `mmui`
UIA tree, clicks 朋友圈, then acts on UIA feed items:

```python
from wechatauto import WeChat

wx = WeChat()
moments = wx.Moment                 # None if the UIA tree is unavailable
if moments is None:
    raise SystemExit("UIA tree unavailable")
wx.SwitchToMoments()
items = moments.GetMoments()
moments.Like(items[0])                            # thumb up
moments.Like(items[0], cancel=True)               # undo
moments.Comment(items[0], "Nice!")                # comment
moments.Comment(items[0], "Thanks!", reply_to="张三")  # reply
```

Runnable demo: `python -m wechatauto.demo_moments_interact [--like N | --unlike N | --comment N 文字]`

> **⚠️ Comment/reply automation is experimental — testing only.** The
> reply-to-a-comment feature (`ReplyComment`) locates the comment row on screen
> via OCR (WeChat's comment area is self-drawn) and then drives the UI to
> click / paste / send. Layout varies across versions and it is not
> production-grade — use it only on a test account to validate the pipeline.
(plain run lists the latest feeds without touching the UI).

## 🧠 How It Works

- **Reading** — WeChat 4.x stores everything in SQLCipher 4 encrypted SQLite databases under `xwechat_files/<wxid>/db_storage/` (`contact.db`, `message_*.db`, `media_0.db`, `sns.db`, …). Each DB has its own 32-byte key living in the Weixin.exe process memory (`com.Tencent.WCDB.Config.Cipher` config objects). The library locates them with a **read-only memory scan**, validates candidates with SQLCipher HMAC rules, decrypts pages to a temp dir and caches the result (first decrypt ~6s, then instant). WAL incremental merging with frame-salt filtering prevents `database disk image is malformed` corruption.
- **Sending** — WeChat 4.x chat UI is self-drawn (no accessibility nodes), so sending uses a hybrid driver: hot-activate the **Qt accessibility gate** inside Weixin.dll (RVA scan, writes the screen-reader flag) to materialize the `mmui::*` UIA tree — search box, `chat_input_field`, etc. Sending is **UIA-first, coordinate + OCR fallback**: auto-calibrating layout (`~/.wechatauto/layout-<machine>.json`), zoomed OCR (3x) with multi-round voting for rare Chinese characters, clipboard + Ctrl+V input to dodge IME interception.
- **Media** — image `.dat` files are `[6B sig][4B aes_size][4B xor_size] + AES-ECB + plaintext + xor` chunks. The account-level AES key is transient (only resident in memory while viewing an image); `MediaDownloader` scans for it, validates via JPEG/PNG magic, and **persists it to `image_keys.json`** so later runs need no scanning (or pass `image_key=` explicitly). Voice is plain SILK read from `media_0.db`; files are read from `msg/file/` with original names resolved from `message_resource.db`.

## ⚖️ vs wxauto

| | wxauto | wechatauto-replica |
|---|---|---|
| WeChat 4.x | ❌ UIA tree gone → broken | ✅ DB decryption + UIA hot-activation |
| Message reading | via UI tree | via local DB (full history, faster) |
| Sending | UIA clicks | UIA-first + OCR fallback |
| Media | limited | image AES decrypt, SILK voice, files |
| Moments | read | read (posting dropped: self-drawn UI) |

## ⚠️ Known Limitations

1. **WeChat must be logged in** — DB keys live in process memory; cached after first extraction, re-extracted automatically after re-login.
2. **Image AES key is transient** — only resident while viewing an image; persisted to `image_keys.json` once found, or inject via `image_key=`.
3. **Sending is a GUI operation** — fails cleanly when the window is locked/unresponsive (operations return a clear failure).
4. **Videos** are downloadable only when the mp4 already exists on disk (`msg/video/`).
5. **Group-chat image originals** are stored locally only after being opened (viewed) in WeChat; until then only the thumbnail (`_t.dat`) exists — `download_image` falls back to the thumbnail (marked `_thumb` in the filename).
6. **Moments posting is dropped** (4.x self-drawn UI, unreliable); reading/likes/comments are supported.
7. **Quote-message sending (BETA)** goes through a coordinate + OCR + `SendInput` pipeline that depends on WeChat 4.1.x self-drawn layout; positioning may drift with window size / DPI / chat content — test flow on a throwaway account only.

## 🗺️ Roadmap

- Calibrate and verify file/image/reply/@ sending on unlocked desktops
- Video message download (4.x storage location TBD)
- Performance: parallel export / first-scan, incremental memory-scan cache

## 📝 Changelog

### v1.2.4 (2026-09-24)

- **Adapted to WeChat 4.1.15.13** (the client auto-updated mid-round; `Weixin.dll` 198,060,584 → 201,552,944 bytes). Three separate things had to change, and each one looked like the others from the outside:
  - **The search entry is now collapsed by default.** What used to be an edit box is a `mmui::XButton` named 搜索 (measured `[314,84,370,140]`); the `mmui::XValidatorTextEdit` only appears *after* that button is clicked. `_search_box(expand=True)` clicks it once and waits for the box; `search_box_rect()` still resolves on the collapsed button (it returns the button rect as a read-only anchor, deliberately **without** clicking — an anchor must not have side effects) while `open_chat()` asks for the expanding variant. On builds where the box is already resident, nothing extra is clicked. 4.1.13.x keeps working; which branch is taken is asserted offline.
  - **The accessibility gate RVA moved** to `0xb135c38` and is in the version table, so the vectorised scan from 1.2.3 is not needed on the common path (it still is for unlisted versions, and results are cached in `~/.wechatauto/gate_cache.json`).
  - **Entering Moments needed an explicit wake-up.** When the `mmui` tree had not materialised yet, `_switch_to_moments_new_style` failed with "找不到导航按钮" — which reads exactly like "this WeChat version is unsupported" and sent me down the wrong path first. It now checks tree readiness, calls `ensure_materialized(timeout=6.0)`, and **re-anchors the root control afterwards** (the old handle points at the empty shell from before the wake-up).
- **Fixed: Moments scrolling never actually scrolled — `find_moment` only appeared to work.** `_send_scroll()` posted a `MOVE` and a `WHEEL` `SendInput` back to back with **zero pause** between them. Wheel events are delivered to whatever window is under the cursor *when they are dequeued*, so the wheel landed on the old position, i.e. on the wrong window entirely; and the mmui timeline ignores the wheel unless WeChat is the foreground window. Now cursor placement is verified (`SetCursorPos` → `GetCursorPos` poll, ≤8 tries ≈0.4 s) before a 0.3 s settle, and `_scroll()` brings the window to the foreground first. Measured on the live client: `find_moment` hit 2/2 runs (51.4 s / 14.2 s), later 17.2 s — before this it was a coin flip that looked like a timing fluke.
- **Moments like / comment now have one implementation.** There were two parallel routes; the one actually exercised by the demos (locate the 「…」 button, then click the 赞 / 评论 in the float layer) was not what `LikeMoment` / `CommentMoment` used. `Like` and `Comment` now take the float route first and fall back to the legacy right-click menu only when it fails; `LikeMoment` / `CommentMoment` delegate instead of carrying their own copies. The legacy comment window (`MomentCommentDialog.send`: click box → `Ctrl+A` → paste → 发送) also **bypassed the 1.2.3 throttle entirely** — it is gated now, placed after the preconditions so a rejected comment does not consume a write slot. Verified on the live client: like and comment both landed, and the comment is present when the post is read back from the local Moments database. **Lesson kept: two implementations of one feature means the audited one is not the shipped one.**
- **Fixed: `find_moment` could hand back a cell that nothing can be clicked on.** UIA recycles a cell handle once its row scrolls out of the viewport, and the recycled node reports `BoundingRectangle == (0,0,0,0)`; every coordinate action on it then raises `Can not move cursor`, which surfaced as "未能打开朋友圈操作菜单" **after a successful locate**. A hit is now checked before it is returned: `_rect_usable()` (unreadable / degenerate rect), `_reattach_item()` re-claims the same post among the currently visible cells by nickname + content prefix + time and swaps the handle **in place**, so the object the caller is holding stays valid, and `_settle_item()` scrolls it back into view once if no live twin is on screen. Re-claiming is deliberately conservative: a *different* post by the same publisher is never adopted (that would like the wrong thing), and a post with neither text nor timestamp — bare nickname — is refused outright. Content is compared by "first 10 characters contain each other" rather than by exact signature, because the UIA summary truncates the body while the DB-corrected body is longer; an exact signature would fail to recognise its own post. Both `find_moment` hit sites, `_locate_more_click` (per retry) and `_invoke_action_menu` (before it touches anything) go through this.
- **Fixed: a real concurrency crash while watching all conversations.** Two threads reaching the same database at once wrote to the same `dst.tmp` scratch file: `FileNotFoundError` from `os.replace`, thrown out of the listener. `db._open()` now takes a per-database build lock, names its scratch files uniquely (`dst.<pid>.<thread-id>.tmp`) and sweeps scratch left behind by a killed process (older than 600 s).
- **Fixed: `get_messages()` was not fail-closed on bad paging arguments.** `limit=0` returned a full page and `offset=-1` returned the **last** row (Python slicing, not a database error), which silently produced wrong answers instead of an empty list — found while the live listener was paging a short session. `limit <= 0` or `offset < 0` now return `[]`.
- **Added: `AddListenAll()` really watches every session.** The global callback used to be registered **per session**, so any conversation that already had its own `AddListenChat` handler was skipped and never saw the global one; de-duplication is now by callback, newly discovered sessions are attached during polling, the pseudo-chat handed to the callback is usable (its `nickname` resolves, `chat` is created lazily, `SendMsg` works), the global callback is re-attached when the listener restarts, and `RemoveListenAll()` detaches for real. Documented in `GUIDE.md` §4.3. **Known cost** (not fixed this round): on an account with ~200 sessions, registering takes ~11 s and each poll round ~10 s, because every session is opened and read; that also makes it a suspect for the high-I/O report in issue #25.
- **Live verification** (real client, `calm` rhythm profile, actions spaced out): send to File Transfer Helper with verbatim database read-back twice (new `sort_seq` matched exactly); `back_to_chat_tab()` + `open_chat()` end to end (7.2% / 8.3% whole-window pixel change as the objective "the page did switch" oracle); Moments locate → like → comment confirmed on screen and in the database.
- **Regression coverage**: `tools/selftest.py` gained a `moment` group (30 checks: fake controls **and** real `MomentItem` instances, all offline — no WeChat, no clicking), plus the `click` (8) and `listen` (7) groups added during this round; the whole suite is **175 checks / 0 fail** (147 before this fix). Two mutations confirm the new group bites: make `_rect_usable` always return `True` → 15 checks red; make post re-claiming compare only the nickname → 5 red, and they are the "must not adopt a neighbour" ones.

### v1.2.3 (2026-09-22)

- **Added: a human-pacing layer, `wechatauto/rhythm.py` — on by default, and it changes default visible behaviour.** This account tripped WeChat risk control once (2026-09-21, forced re-login mid-session), so from now on every action that drives the real client has to move like a person, and it had to be enforced in the library rather than in a demo script. What risk control sees is the **time distribution of actions**, not coordinates: constant intervals, a cursor that teleports, clicks that always land dead centre, perfectly uniform typing, several writes per second. Now `nap()` jitters every wait (multiplier floor pinned at 1.0 — it only ever *lengthens* the fixed sleeps that hold render stability), `move_to()` walks the cursor along a quadratic bezier and `point()` picks a random interior target after insetting all four edges (no more `BoundingRectangle` centre hits), `type_gap()`/`key_hold()` break uniform keystrokes, and `gate()` throttles **outward-visible writes only** (send / send-file / like / comment / recall / poke / voice call) with a minimum interval plus a rolling-window burst cap. Read paths (database, screenshots, OCR, control lookup) are never throttled. Profiles `natural` (default) / `calm` / `fast` / `off`; `off` reproduces pre-rhythm values exactly and is for control experiments only. Override with `WECHATAUTO_RHYTHM`, `WECHATAUTO_WRITE_GAP`, `WECHATAUTO_WRITE_BURST`. Throttle state is persisted to `~/.wechatauto/rhythm.json` so it also holds **across processes** — every demo script is a fresh Python process, which makes in-memory rate limiting worthless. On the default profile two sends are at least 2.5–6 s apart and the 7th write inside 120 s enters a 30–75 s cool-off. Cross-process is documented last-writer-wins; no named mutex was added on purpose.
- **Fixed: the UIA gate scan could stall `quick_send` for ~8 s and could abort it outright.** A user reported hanging inside `_rip_xrefs_to_rva`'s byte loop (198 MB `Weixin.dll`; measured 6.6–9.8 s here, which looks like a deadlock on slower machines), and nothing on that path caught errors — any exception propagated out of `ensure_window` and killed the whole `quick_send` call. The scan is now numpy-vectorised: identical candidates on the real DLL (`0xae2b0c8`) at 0.56–0.77 s (~12x), with the old byte loop kept as the no-numpy fallback (`numpy` arrives via `opencv-python`, so a normal install never takes it; if it is missing the scan is only slower, never absent). Scan results are cached on disk in `~/.wechatauto/gate_cache.json` keyed by DLL identity (version directory + size + mtime), **including negative results** ("scanned, no candidates" — unsupported versions were paying the full rescan every launch); a second scan in the same process is 0.000 s, and a verified gate RVA is restored from disk and put first in the candidate list. Unreadable file / not-a-PE now return an empty sequence instead of `None` (callers iterate directly) and are not written to disk. Exceptions during hot activation now degrade to "skip this round, fall back to OCR/coordinates" instead of breaking the send; `KeyboardInterrupt` is a `BaseException` and still propagates, so Ctrl+C keeps working.
- **Fixed: the older Moments comment path bypassed the throttle entirely.** Comments have two parallel implementations; the previous round gated only the newer template-matching one (`_click_comment_send`), while `Moment.Comment` still went through the old UIA comment window (`MomentCommentDialog.send`): click the edit box → `Ctrl+A` → paste → click 发送, with not one step throttled. That is now gated, placed after all precondition checks so a failed precheck does not consume a write slot. **The lesson: audit outward writes by the action they perform, never by function name.** `tools/selftest.py` now scans for the action itself (`SendKeys('{Enter}')` / `press_enter` / `keybd_event` / clicking 发送) and fails if such a function has no `rhythm.gate`, with an explicit whitelist for functions that merely reference the send button or produce nothing outward (button lookup, pinyin candidate selection, OCR matching, Enter-to-open-chat). Deleting this one gate turns 3 checks red and names `moment.py:MomentCommentDialog.send`. Every remaining write entry point was traced to a gated sink: `quick_send`→`send_msg`→`click_send`, `quick_quote`/`at_member`→`click_send`, `send_text_to`→`send_text`, `ReplyCommentMoment`→`ReplyComment`→`_click_comment_send`, `wx.SendMsg`→`send_msg`, `sender.send_to`→`send`.
- **Live re-verification (2026-09-22, real client, `calm` profile, actions spaced out)**: a send to File Transfer Helper passed with verbatim database read-back (new `sort_seq` matched exactly, throttle ledger recorded one write, hybrid-path log confirms the UIA driver was active); `back_to_chat_tab()` + `open_chat('文件传输助手')` passed end to end.
- **Known issue (not fixed this round, and it corrects a v1.2.2.6 claim)**: v1.2.2.6 stated that clearing `WS_EX_TRANSPARENT` around the event makes UIA clicks land. **That effect did not reproduce on this machine.** Measured: the style is genuinely cleared (`ex=0x80000`), yet `WindowFromPoint` still returns the plain `Qt51514QWindowIcon` main window instead of the render sub-window at the nav-tab point, and the click changes nothing (0.0% whole-window pixel diff); `Control.Click()` and `Invoke()` are equally inert. When that style was left cleared across processes, the very same point *did* switch pages (15.3%) — so the hit-test decision involves more than the `WS_EX_TRANSPARENT` bit (`MMUIRenderSubWindowHW` is a layered window, where per-pixel alpha plausibly matters). Search-box / `ValuePattern` routes are unaffected, so sending, searching and opening chats all work; **coordinate clicks on the nav bar remain unresolved**. This round changed no code in that area, and the "fixed" claim has been removed from the record rather than left standing.
- **Regression coverage**: `tools/selftest.py` gained a `gate` group of 35 checks (synthetic PE fragments + temporary cache files + fake window handles, fully offline, never touches WeChat) and the `rhythm` group grew to 42 with the action-based throttle audit; whole gate 131 checks. Three mutation checks confirm the tests bite: delete the no-numpy fallback → the group crashes; drop the displacement sign-extension → 6 fail; delete the comment gate → 3 fail and name the function.

### v1.2.2.6 (2026-09-21)

- **Fixed: `calibrate_layout()` always raised `NameError` in the published 1.2.2.5.** The timeout wrapper `_run_with_timeout` in `guia.py` uses `threading.Thread`, but the module never imported `threading` (checked against the 1.2.2.5 wheel: `threading.Thread` present, `import threading` absent). Layout calibration is the entry path of the UIA driver, so every pip-installed user hit it on the first calibration; local checkouts were synced separately and hid it. The two layout profiles failed **differently**, which is why one report was not enough: on `wide` calibration returned `False` and wrote no layout file at all, while on `portrait` the send-button probe swallowed the same error and calibration returned `True` on default ratios. Both shapes were reproduced by deleting the module attribute from a synced copy. A probe that errors inside the timeout wrapper now leaves a log line, and the outer handler separates code defects (`NameError`/`UnboundLocalError`/`AttributeError`/`TypeError`/`ImportError` → `wxlog.error`, reaches the console) from recoverable misses (OCR anchor simply not found → debug, falls back to defaults by design). Adding the import does not make OCR find the anchor — that stays a fallback, not a failure.
- **Fixed: send verification could confirm a message that was never actually sent** (`_verify_sent`). Two independent holes: it matched on **substring**, so a draft left in the chat input (the body that actually went out read `校准wechatauto 部署自检 OK`) still verified a call for `wechatauto 部署自检 OK`; and with **no watermark**, an older self-message containing the target text among the last rows validated even when this send produced no row at all — the UI returning success only leads to polling, never to a resend, so a stale row is accepted on the first check. Plain-text sends now require a **verbatim** body match (`strip()` is not verbatim); reply / quote / `at_member` keep substring matching because WeChat wraps those bodies, and that rule is now explicit per call site. Every verified send first takes a **pre-send watermark** of the target chat: the max `sort_seq` *plus* the `(sort_seq, local_id)` identity set of the top rows, because real `sort_seq` values tie heavily (up to 8 rows in one chat) and a bare `>` would reject a genuine send. Verification also resolves the display name to a `username` before reading: message tables are keyed by `username` and a wrong one returns `[]` **silently**, so group-chat verification had been failing closed for the wrong reason. When no watermark can be taken (fresh chat, DB unavailable) verification falls back to the unwatermarked check rather than reporting failure.
- **Regression coverage**: `tools/selftest.py` gained an offline `verify` group (14 checks against a fake DB) and an offline `calibrate_layout` group covering both profiles and the anchor-hit path (8 checks against a fake window) — 36 offline checks, full gate 55 pass / 0 fail. The verifier's rules were additionally replayed read-only against the live decrypted database (no message was sent); the end-to-end send path still needs a real take.
- **Fixed: a UIA click could land on whichever window sits behind WeChat.** WeChat's content window (`MMUIRenderSubWindowHW`) carries `WS_EX_TRANSPARENT` (measured `exStyle=00080020`), so a raw `mouse_event` at its coordinates is skipped by hit-testing and delivered to the plain `Qt51514QWindowIcon` window behind it — this is why UIA clicks looked ignored on 4.1.13+. `uia_driver` now clears the extended style around the event and restores it immediately (`_click_at` / `_click_ctrl`), the way `guia.wx_click` already did. The mouse wheel is the exception: it works on that window unchanged.
- **Fixed: `open_chat` could not recover when WeChat was parked on the Moments page.** The session list does not exist there, so the search-based path never resolves — measured as an 8+ minute spin at high CPU, and separately as a ~90 s give-up that returns `None` with nothing on screen explaining it (it cost a whole recording take). `WeChatUIA.back_to_chat_tab()` clicks the `mmui::MainTabBar` 「微信」 item. It cannot ask which page is showing: on this build `XTabBarItem` exposes no selection state (plain `ButtonControl`, no `SelectionItem` pattern, `LegacyIAccessible.State` always 0) and the per-page controls stay in the tree after a switch, so the tab is clicked unconditionally — clicking the already-selected tab only scrolls the session list back to the top.
- **Fixed: the search-box calibration ratio could paste a chat name into a live conversation's input box.** The stale `SEARCH_BOX_RATIO` resolved to a click point off the real box (computed center 212,120 vs a measured box at 250,152–412,192), so the clipboard paste went to whichever chat was open. `_search_chat` now prefers the UIA `search_box_rect()` and self-checks afterwards: if the text did land in the chat input it is cleared through `ValuePattern.SetValue('')` and the search fallback is abandoned — nothing is sent.
- **Added to the UIA driver**: `search_box_rect()` and `_set_text()`. `ValuePattern.SetValue` drives WeChat's live search with no keystrokes and no clipboard, but it does **not** give the Qt widget focus, so it is used for the search box only — the send path still needs a focused input and `{Enter}`.
- **Privacy fix**: `media.py` no longer prints 12 bare `[DBG]` lines to stdout, one of which carried the absolute `.dat` path containing the account **wxid**. They are `wxlog.debug` now (the console handler is INFO by default), and the two silent `return None` bail-outs became warnings.
- **Still unverified on a live client this round**: whether `_click_ctrl` and `back_to_chat_tab` actually land the click on WeChat's content area (the relogin interrupted that check). The `WS_EX_TRANSPARENT` measurement and the search-box rect numbers are real; the end-to-end effect of the two new click paths is not yet demonstrated.
- **Thanks to [wenjiavv](https://github.com/wenjiavv)** for reporting both of the above with reproductions, a per-profile symptom split and fix proposals ([#28](https://github.com/fanyuantaier/wechatauto-replica/issues/28), [#29](https://github.com/fanyuantaier/wechatauto-replica/issues/29)).
- **Fixed: `RecallGuard` could not see a single revoke — two independent faults.**
  1. **Revoke rows were never delivered.** WeChat rewrites the original row in place (across 208 local sessions, 64 `revokemsg` rows: `revoketime - create_time` lands in 1-30 s for 10 of them, 31-300 s for 54, and zero for none), and `local_id` / `create_time` stay those of the original message. `Listener` increments by `sort_seq > watermark`, which never changes on a rewrite, so no revoke event is emitted. `watch()` now runs a `wxrecall-scan` daemon thread that re-reads the last `scan_limit` (default 30) rows per session every `scan_interval` (default 2.0 s) and diffs `local_id` against the mirror: normal in the mirror, `revokemsg` in the live DB = one revoke. `scan_now()` is exposed for scripts.
  2. **Even when delivered, the original was never found.** `_find_original` used `create_time < revoke_time`, where `revoke_time` is the revoke row's own `create_time` (= the original's timestamp) — the strict `<` excluded the only matching row. Lookup is now exact by `(chat, local_id)` first (the rewritten row keeps its `local_id`, so the mirror row with that id is necessarily the original), with the time window only as a fallback and relaxed to `<=`.
  3. `on_msg` no longer mirrors revoke rows — storing one would overwrite the original it is meant to rescue.
  4. The revoke timestamp now parses `<revoketime>` (it used to print the original send time); `(chat, revoke_time)` is the dedup key, so the Listener and polling paths cannot double-report and a restart cannot re-report history.
  5. `close()` now stops the polling thread.
  Measured: offline 3/3 real revoke rows (local_id 62/64/88) restored, a second `scan_now()` returns 0 rows (dedup works), only `MainThread` left after `close()`; the live path (send → mirror 20→21 → revoke → restore) passes too.
- **Security fix: `demo_media.py --list` printed media XML verbatim**, exposing `aeskey`, `cdnthumbaeskey`, `cdnthumburl` and `md5`. It now prints only dimensions, byte size, duration and file name.
- **Added: OCR fallback for Moments likes/comments under WeChat 4.1.13's merged layout** (`Moment._read_comment_cell_ocr`). Likes and comments now live in a sibling `mmui::TimelineCommentCell` that never enters the UIA tree, so parsing the text cell always came back empty; an empty UIA result now falls back to "comment-box region screenshot + OCR". The comment-box search area also moved to 320 px above the viewport bottom (the old `bottom+8` band landed on the taskbar and never matched on 4.1.13).
- **Cleanup**: `demo_send.py`'s `pick_default_image` docstring is now a raw string, silencing a `\W` escape warning.

### v1.2.2.5 (2026-09-19)

- **Fixed: cached Moments pictures decrypted into files nothing could decode.** Two independent causes on the same `.dat` v2 path:
  1. **Wrong single-byte XOR key for cache containers.** That key is the low byte of the account's config dword, but the code derived it per file from the plaintext's last two bytes (`tail ^ 0xFF == FF D9`). WeChat appends a **24-byte footer after the image end marker** in Sns cache containers (189/295 measured here), so the check failed and it silently fell back to a wrong key — the whole tail segment came out garbled. Resolution order is now **config dword (authoritative) -> thumbnail statistics -> fallback**, resolved once per account.
  2. **The footer was kept as image data.** Decrypted output is now trimmed at the JPEG/PNG end marker, so a strict decoder no longer rejects an otherwise valid picture over trailing bytes.
  Measured: Sns cache containers passing `MediaDownloader.decrypt_image()` **118/295 -> 295/295**; the Moments cache index's decrypt failures **177 -> 0**; a 15,577-file chat-image sample **429 JPEG + 171 wxgf, 0 failures** (chat media unaffected, wxgf/WXAM containers untouched).
- **Fixed: `MomentDB.find_local_media`'s size guard never ran on its most common path.** The "reject an impostor by size deviation" check only existed on the multi-candidate branch; with exactly one same-dimensions candidate the code returned without comparing anything, so a 66 KB mismatch passed silently. It now logs the deviation and deliberately still **does not** reject: the declared `totalSize` is the CDN original while the cache holds WeChat's re-encoded copy, so a large delta is normal and is not evidence of a wrong image (the multi-candidate rule is unchanged).

### v1.2.2.4 (2026-09-18)

- **Fixed: a wrong key form could make an entire message shard unreadable.** A cached key could be stored as 48 bytes (32B key + 16B explicit salt), but decryption picks its branch by **key length** — 48 bytes takes the “plaintext header” branch and produces a file whose header is not SQLite (`file is not a database`), making that shard (a 96 MB `message_0.db` in practice) completely unreadable. Three guards now: **verify the standard form first when storing** (store a bare 32-byte key unless the DB really uses a plaintext header), **normalize on read**, and **auto-correct legacy entries when loading the cache**.
- **Fixed: “database merge failed” was raised outright while WeChat keeps writing.** The old code wrote decrypt results straight onto the cache file and raised on failure, destroying the last usable copy. Now: build a **self-consistent main-DB snapshot** as a floor (verified with `quick_check`, re-read up to 4 times) → then try merging WAL frames on a copy (fall back to the main snapshot with a warning) → all intermediate files are written to a temp path and **atomically replaced only on success**, so a failure never destroys the previous usable copy.
- **Fixed: leftover cache entries for databases that no longer exist crashed construction** (`KeyError`) — now fully tolerated.
- **Layout: added a phone-style portrait profile** (dual profiles `wide` / `portrait`), auto-selected by window aspect ratio, each calibrated and stored independently (old flat files migrate automatically). Also fixed **session lookup in portrait mode** (the name-column filter discarded every session name as an “avatar area”, so `find_session` always returned None).
- **Cleanup**: removed 10 unused imports; added debug logs to 8 silently-swallowing handlers (a probe failure must not masquerade as a normal result); `demo_send.py` no longer hardcodes another user’s path or a real wxid (default image auto-discovers RWTemp); real wxids in READMEs replaced with placeholders.
- **New `tools/selftest.py`**: read-only self-check (layout / keys / sessions / messages), run in one command.

### v1.2.2.3 (2026-09-16)

- **Fixed: constant ~50 MB/s disk read + write while the library runs.** The decrypt-cache stamp compared mtimes with exact float equality while writing them with `%f` (6 decimals) against Windows' 7-decimal mtimes — so every poll (~1s) looked like a changed database and re-decrypted everything (WAL merge + cache rewrite included). Now `STAMP_VERSION 3` with `%r` (exact round-trip): one rebuild after upgrading, then stable.
- **Message reads now LIMIT inside each shard before merging** (**5.5×** on a 48k-message group: 1.053s → 0.191s; `get_new_messages` ≈6×). Huge chats no longer materialize every shard's rows in Python. Public APIs (`get_messages`, `get_new_messages`, `get_message_row(..., local_type=)`, `get_message_rows_for_media`) keep identical signatures **and** results (verified across 6 chats × 71 cases).
- **Fixed “cannot get keys” under UTF-8 mode**: four `tasklist` calls decoded GBK output with the default codec; under `python -X utf8` / `PYTHONUTF8=1` the decode failed, left `stdout` as None and raised `AttributeError`, killing key extraction. All four now use `encoding="gbk", errors="replace"` with a None guard.
- **`WeChatUIA.is_running()` is now multi-criterion**: it used to be one probe wrapped in `except → False`, so any error silently became “WeChat is not running”. It now checks tasklist / main-window title / psutil, and only writes an explicit stderr note when every probe *errors*.
- **Real contact/group names removed from demos and docs** (replaced with 「文件传输助手」; 「兔仔仔」/「送你挖银子」 kept as sample defaults).

### v1.2.2.2 (2026-09-13)

- **Key handling hardened: no more recurring failure after every WeChat update.** Three layers:
  - **The cache can no longer be wiped**: `_save_keys()` never persists an empty result (atomic write + `.bak` kept). Previously a transient extraction failure (wrong account / permission) **overwrote a good cache with an empty file**, so every later start reported "0 keys" — that is exactly the `keys cached: 0` seen in the field.
  - **Durable key copy**: a copy is kept at `%LOCALAPPDATA%\wechatauto_keys\<account>.json` (override the directory with the `WECHATAUTO_KEYS_DIR` env var, e.g. your project workspace), surviving TEMP cleanup and WeChat updates. On startup the caches are **merged from several locations** (durable copy → work cache → `.bak` → other accounts' caches) and every entry is verified against page-1 HMAC, keeping only working keys.
  - **Account selection is now decided by key verification**, not by "most recently modified .db" (a WeChat update rewrites every .db, shifting mtimes and picking the wrong account → 0 keys). One memory scan now collects candidate key material and scores **every account directory** by page-1 HMAC, switching to the one that unlocks (log: `已按密钥校验选定账号目录: …`).
- **cfg master-key warning**: on WeChat 4.1.13+ the cfg path returns an **untrustworthy master key** (demoted to a fallback since v1.1.9); it now logs an explicit warning when it cannot reproduce any database key instead of silently succeeding.
- **Better diagnostics (`diagnose_keys`)**: now prints the WeChat client **FileVersion**, per-account "cache / derived" availability and a **master-key consistency check** (which tells you which account the keys belong to); the `_open` error text now lists the three classic causes (32-bit Python / permission mismatch / wrong account among several) plus the `account=` hint.

### v1.2.2.1 (2026-09-12)

- **Compatibility with the new WeChat UI (verified on 4.1.13.65)**: the new build changed `AutomationId` from short names into **dotted paths** (old `session_list` / `chat_input_field` → new `MainView.main_tabbar`, `MainView….main_window_sub_splitter_view…`), which broke exact-equality matching. AutomationIds are now matched as exact / dotted-segment / suffix (`_aid_hit()`), so both the old short names and the new paths resolve.
- **Relaxed window-title matching**: the new main window title is `Weixin`, and becomes `微信(3)` when there are unread counts; `_title_is_main()` now matches by containment and still rejects unrelated titles such as `WeChat`.
- **Anchor candidate lists + structural fallbacks**: the main window / login window / search box now match against candidate tuples (single-value constants kept for backward compatibility); the search box, chat input and search-result list each gained a structural fallback (an EditControl whose Name contains 搜索, an EditControl inside the chat area, attribute-based search from the root), so a renamed class or AID in a future build no longer breaks the whole path.
- **New layout self-check `WeChatUIA.describe_layout()`**: one call returns the main class name, window title, layout kind (`merged` / `legacy` / `chat`) and the resolution result of every anchor (main_window, search_box, session_list, chat_input, main_tabbar, sns_list). Run it first when a new WeChat build changes the UI.
- Note: the Moments anchors were already dual-layout (standalone `mmui::SNSWindow` / merged `mmui::SNSContentView`); 4.1.13.65 keeps those class names, so no change was needed there.

### v1.2.2 (2026-09-12)

- **Fix cross-shard message reads (missing messages / voice)**: a conversation's `Msg_<md5>` table actually spans several `message_*.db` shards, but `get_messages` only hit the first one — e.g. a chat with 8,904 real messages (24 voice notes) reported just 1. New `_find_msg_tables()` / `_msg_conns()` / `_shard_rows()` merge reads across all shards and sort by `sort_seq`; `get_messages`, `get_new_messages` and `_find_media_rows` now use the merged view. `get_message_row` gained a `local_type` filter (a `local_id` is **not** unique across shards) and new `get_message_rows_for_media()` returns every shard row; media downloaders pass their type code so the right shard row is selected.
- **Reliable listener delivery (behavior change)**: the watermark now advances **only after callbacks succeed** (a new `_inflight` boundary prevents re-dispatching unconfirmed messages), callbacks are retried (`max_retries`, default 3) before being logged as dropped, and the watermark is persisted to `listener_watermark.json`. Messages that arrive while your process is down are delivered on the next start instead of being skipped. Pass `watermark_file=""` to disable persistence.
- **Text restore no longer requires CJK**: pure English / digits / URLs / emoji container-format messages are decoded instead of degrading to `[文本]`.
- **`Chat.GetNewMessage()` no longer drops backlog**: batches are pulled until caught up (>200 messages) and the watermark only moves to the last message actually returned, instead of jumping to the newest DB position.
- **UIA materialization self-heal (no child controls after a WeChat restart/upgrade)**: after a WeChat restart or upgrade the Qt accessibility gate byte resets to 0 and the `mmui::` tree degrades to an empty Qt shell (`Qt51514QWindowIcon` + 2 nodes). The driver now hot-writes the gate, **verifies that `mmui::` controls actually materialized**, and retries other candidate RVAs on failure (the RVA that worked is cached per DLL identity). `_get_uia()` self-heals on a 30s throttle — no more "one failed wake and OCR forever", and no manual `refresh=True`. Fallback table gained `4.1.13.65 → 0x0AE2B0C8`.
- **Moments scroll-positioning fixes**: bounded reversals (at most one per run, then downward-only) and stall detection (the top-cell fingerprint now includes geometry — merged-layout ListItems can share the same Name, which previously looked like a stall and aborted mid-scroll); skip "scroll to top" when the DB ruler says the target is below; the stop criterion is now **"the next moment's UIA control appeared"**; direction/distance fixes (clipped pixels → wheel notches) plus a bottom margin so the "…" button is reachable.
- **Message type table**: 4.x composite `local_type` is decomposed by its low 32 bits; added `50 音视频通话` (VoIP bubble), `11000 动画表情`, `8594229559345 红包` (the library previously mislabeled it as an appmsg/file card via the low-byte mapping); empty bodies (stickers) now show `[动画表情]` instead of a blank line; `demo_group_messages` decodes zstd for every type and prints one-line summaries.
- **`demo_listen.py --all`** now auto-discovers new sessions (previously limited to the 30 most recent at startup).
- **New anti-recall listener `RecallGuard` (BETA)**: after `watch(listener)` every new message is mirrored into a local sqlite DB and attachments (image/voice/video/file) are backed up to `media/`; on a `revokemsg` it prints `[撤回] <revoker> → <original text>` and records it in `recall_events`. **Not fully field-tested — shipped as BETA.**
- **New `MomentObserver` (BETA)**: observe-and-freeze snapshots of Moments cache keys via `snapshot()` / `diff()` (cache keys have no derivable mapping to feed md5 and the cache is evictable, so observing is the only way to keep them). **Not fully field-tested — shipped as BETA.**

### v1.2.1 (2026-09-06)

- **New "quote & send" message feature (BETA)**: `WeChatGUI.quote_msg(text, who, target_text=None, verify=False)` right-clicks the target message → picks「引用」from the popup menu → types the content → sends; omitting `target_text` quotes the most recent message. `quick_quote()` is a one-liner entry point, demo script `wechatauto/demo_quote.py`.
  - **BETA disclaimer**: the feature uses a coordinate + OCR + `SendInput` pipeline that depends on WeChat 4.1.x self-drawn layout; positioning may drift with window size / DPI / chat content. The right-click uses `SendInput` injection (the render window ignores `mouse_event` right-clicks), and the cursor is first moved with `SetCursorPos` before injecting the click to avoid "moves but doesn't click / clicks but doesn't move" drift.
- **Removed the `desktop_available()` white-pixel screen check**: control targeting is fully UIA-based now, so the full-window screenshot white-ratio sampling was dropped — it could falsely report "window not visible" while WeChat was fine. `ensure_visible()` now treats a live window handle as visible and keeps its "minimize blockers + bring-to-front" actions.

### v1.2.0.3 (2026-08-31)

- **Fix WAL-merged database cache corruption causing infinite loop**: `_check_merged` previously used `SELECT count(*) FROM sqlite_master` which only checks the schema tree — corrupted data pages still passed validation, causing the cache stamp to mark the bad cache as "up-to-date" and every subsequent poll to reuse it, throwing `database disk image is malformed` on a dead loop. Now uses `PRAGMA quick_check` for full database validation (data + index pages). New `_invalidate_cache()` clears all decrypted `.db`/`.stamp` files. New `_run_msg_query()` unified entry point auto-retries once on `malformed` (clear cache → rebuild → retry). `_msg_conn` now closes shard connections immediately to avoid Windows file-lock issues during cache cleanup.

### v1.2.0 (2026-08-30)
> Note: this release merges all changes made after 1.1.10.2 that were not yet published (1.1.10.3 → 1.1.10.7).

- **Smart Moments positioning + auto like**: `Moment.find_moment(publisher, keyword, ...)` uses a hybrid of the **DB route (computing the target offset)** + **UIA route (scrolling by offset)** — it derives how many feeds the target is from the current view using the local `sns.db` ruler, then scrolls adaptively in the correct direction to land on the moment by author/keyword, eliminating blind downward scrolling and false "not found" results.
- **"…" overlay recognition**: `Moment._locate_more_click` / `_find_more_button` locate the "…" button (bottom-right of a feed) via template matching (light/dark templates shipped in `assets/`) and click it; if not found it keeps nudging the scroll and retrying to pop up the like/comment overlay.
- **One-shot Like**: `Moment.LikeMoment(publisher, keyword, ...)` does "locate → tap "…" → like in the overlay"; the "赞/Comment" buttons in the overlay are found by a global deep traversal from the UIA root (matching by name) and clicked at their center.
- **Moments like/comment via UIA controls**: `WeChat` now exposes a `Moment` property and `SwitchToMoments()` that hot-activate the `mmui` UIA tree and click the 朋友圈 nav button. `Moment.Like(item, cancel=False)` and `Moment.Comment(item, content, reply_to=None)` operate on UIA feed items — likes/comments are server-side actions, so they need the UI (the DB route stays read-only). `WeChat.Moment` is `None` when the UIA tree is unavailable. Demo `wechatauto/demo_moments_interact.py`.
- **Moments media download**: new `MomentDB.download_media(media, save_dir, kind)` copies a single picture/video from the local cache first (byte-for-byte, offline) and falls back to the CDN url; `MomentDB.download_moment_media(feed, save_dir, ...)` fetches all pictures/videos of one feed into a folder. `find_local_media(md5, kind, size)` locates the cache file by md5 and, for videos, by `totalSize` across the whole `Sns/Video` tree (the video cache name is a content-hash unrelated to the feed md5, so size matching recovers real MP4s). `parse_feed` now distinguishes pictures vs videos via `videomd5`/`videoDuration`/`type` and records each media's `size`. Demo `wechatauto/demo_moments_download.py`.
- **Moments read API (DB route)**: `MomentDB.get_moments()` now supports `since` / `until` (Unix-seconds time filter) and `keyword` (text filter), plus `limit=0` to return every row. New incremental-sync helpers `latest_tid()` / `get_moments_since()` make it easy to poll for new moments. New interaction notifier `get_interactions()` / `interactions_unread_count()` read the "likes/comments on my moments" table (`SnsMessage_tmp3`). New `comment_tree()` / `comment_reply_to()` organize a feed's comments into reply chains (built from `comment_id`/`ref_comment_id`).
- **Add group name ↔ ID lookup**: `get_groups()` now returns each group's real `name` (from `contact` table, falling back to its wxid). New `group_name_to_id(name)` (exact match first, then substring/fuzzy) and `group_id_to_name(chatroom_wxid)` let you resolve a group's wxid from its display name and vice versa — handy for combining with `get_group_members()` and `at_member()`.
- **Add group member enumeration & change watch (read-only, no UI)**: New `WeChatDB.get_groups()` / `get_group_members(chatroom_wxid)` read `chat_room` + `chatroom_member` + `contact` from `contact.db` to return each group's members (username / nick_name / remark / is_owner). New `GroupMemberWatcher` (via `get_group_member_watcher`) snapshots membership and `poll()` diffs against the baseline to report `joined` / `left` members, enabling polling-based membership-change monitoring. Useful together with the existing UI-automation `at_member()`.
- New runnable demos `wechatauto/demo_moment_find.py`, `demo_moment_more.py`, `demo_moment_like.py`; new deps `pyautogui`, `opencv-python`.

### v1.1.10.2 (2026-08-30)
- **Fix long text still showing `[文本]` on fresh installs: add required `zstandard` dependency**: WeChat 4.x stores long-text `message_content` as a zstd-compressed frame, decoded in `_friendly_content` via `import zstandard`. That import silently failed when `zstandard` was absent (it was **not** in `pyproject.toml` required deps), so long text degraded to the `[文本]` placeholder while listening worked normally. `zstandard` is now a required dependency; `_friendly_content` also gained lazy dual-package import (`zstandard`/`zstd`) via new `_get_zstd_module()` / `_zstd_decompress()` helpers.

### v1.1.10.1 (2026-08-29)
- **Fix `AttributeError: 'sqlite3.Row' object has no attribute 'get'` in message reading**: `_msg_row_to_dict` called `.get("compress_content")` on a `sqlite3.Row`, which only supports `[]` access. Messages whose content stays a placeholder (e.g. emoji/special types) hit this branch and crashed the real-time `Listener` polling loop. Now uses `[]` access with a fallback, fixing `get_messages` / `get_new_messages` / `get_message_row`.

### v1.1.9 (2026-08-27)
- **Fix key extraction for WeChat 4.1.13+**: Prioritized `Config.Cipher` memory scan over `extract_master_key_from_cfg` for key extraction. The cfg-based extraction returns incorrect master keys on WeChat 4.1.13.12, while the Config.Cipher scan (which reads raw `enc_key` values from XOR-decoded blobs) works correctly. This fixes the "0/24 keys verified" issue reported on newer WeChat versions.

### v1.1.8 (2026-08-25)
- **Fix missing `_derive_xor_key` method in MediaDownloader**: v1.1.7 release accidentally omitted the `_derive_xor_key()` method while code paths (`_decrypt_v2`, `detect_image_key`) still referenced it, causing `AttributeError` when decrypting images. Restored the method for XOR key derivation from thumbnail `_t.dat` / `_h.dat` files.
- **Fix group-chat `sender_id` → `sender_username` resolution**: `Listener` callbacks now receive `sender_username` (wxid format) in the message dict, resolved from `message_resource.SenderName2Id` mapping. Previously, `sender_id` was a numeric ID that could not be used directly with `search_contact()`.
- **Thanks [uiharukazari0105](https://github.com/uiharukazari0105)** for reporting the missing `_derive_xor_key` issue in v1.1.7.

### v1.1.6.1 (2026-08-20)
- **PyPI description fix**: v1.1.6 was uploaded without the synced `README_pypi.md` (description still showed 1.1.5.1); this patch restores the full v1.1.6 changelog and bumps the version marker.

### v1.1.6 (2026-08-20)
- **Auto-diagnosis on missing key**: `数据库无可用密钥` now runs a built-in check before raising — Python bitness (32-bit can't read 64-bit Weixin memory), per-PID `OpenProcess`/`ReadProcessMemory` permission, and multi-account mismatch (all `wxid_*` dirs vs. picked account, suggesting `WeChatDB(account=...)`). No need to run `diagnose_keys` first.
- **New diagnostic tool**: `wechatauto/diagnose_keys.py` (`python -m wechatauto.diagnose_keys`, WeChat logged in) dumps lib version, Python bitness, Weixin PIDs with per-process read-permission checks, all accounts vs. picked account, cached keys, fresh in-memory extraction, and key verification — paste the output when reporting key-extraction failures.
- **Skip `migrate\unspportmsg.db`**: WeChat's reserved "unsupported message" DB has no in-memory key and is never queried; it was forcing a full process-memory scan on every init.

### v1.1.5.1 (2026-08-18) — beta
- **Fix real-time listening**: `WeChatDB.get_new_messages()` referenced an undefined `found` (NameError swallowed by `Listener._poll_once`), so **no** message callbacks ever fired — including first messages from contacts you had never chatted with.
- **Dynamic message shards**: `_message_dbs()` now re-scans the disk so shards WeChat creates at runtime (e.g. `message_5.db`) are picked up and their keys extracted automatically.

### v1.1.5 (2026-08-18)
- **Version cleanup**: normalized the patch version (1.1.4.2 → 1.1.5) after the `media_*.db` voice fix.

### v1.1.4.2 (2026-08-18)
- **PyPI description cleanup**: removed the demo default-group changelog line from the PyPI description.

### v1.1.4.1 (2026-08-18)
- **PyPI readme bilingual**: merged the Chinese (`README.zh-CN.md`) and English (`README.md`) into one PyPI description so the Chinese version is visible on the package page.

### v1.1.4 (2026-08-18)
- **Voice download across all media databases**: `download_voice()` now searches every `media_*.db` (not just `media_0.db`) — WeChat shards voice data across multiple media DBs; previously voices stored in `media_1.db` etc. could not be found (thanks uiharukazari0105).
- **`demo_media.py --images N`**: download the latest N images of a chat directly from the DB (by local_type), bypassing the total-message `--limit` — no more "only a few images listed" when a group has thousands of messages.
- **`WeChatDB._find_media_rows(user, types)`**: new helper returning all media local_ids of a chat for a set of local_types (batch download).
- **Group-chat image thumbnail fallback**: original images in group chats are only downloaded after being opened in WeChat; `download_image` now falls back to the thumbnail (`_t.dat`) when the original is missing, saving it with a `_thumb` suffix.

### v1.1.3 (2026-08-17)

### v1.1.2 (2026-08-16)
- **UIA driver thread-safety**: `WeChatUIA` now initializes COM on the current thread (`CoInitializeEx`, idempotent) — fixes crashes when instantiated from background threads / host apps (e.g. WeChatBot) with "CoInitialize not called / cannot load UIAutomationCore.dll" errors.
- **Main-window filtering**: only windows whose process loaded `Weixin.dll` are considered — auxiliary processes without the DLL (whose hot-activation always fails) no longer produce noise warnings.
- **Forward-voice fix**: `Chat.ForwardVoiceMessage` uses `self` when no target is given (the previous `_cur()` could resolve the wrong chat).
- **Re-entrant UI lock**: `LockManager` is now re-entrant per thread — `@uilock` functions calling each other (e.g. `ForwardVoiceMessage` → `VoiceMessage.forward_to`) no longer deadlock.

### v1.1.1 (2026-08-16)
- **Recall last message** (`Chat.RecallLastMessage` / `uia_driver.recall_last_message`): right-click the latest own message → UIA-first menu-item click (`mmui::XMenuView` found inside the main-window subtree), OCR fallback; fails cleanly when the 2-minute recall window has passed (menu only shows "Delete").
- UIA robustness: menu-item lookup scoped to the main-window subtree (avoids the Windows UIA root-traversal hang), removed the fragile `WindowControl(ClassName=...)` fallback.
- Media fix: video id bytes→str decoding in `MediaDownloader`.
- `demo_media.py --photos` default 3 → 10.

### v1.1.0 (2026-08-15)
- **Image AES key auto-capture** (`media.py`): the V2 image key is only resident in memory while viewing an image (~5 min). `_scan_aes_key()` gained a `monitor` mode — polls continuously and persists the key to `image_keys.json` once found; users just open one image to finish setup.
- Fixed the process-ordering scan bug (removed the memory-usage sort that pushed the main process last).
- **Forward voice messages**: SILK extraction from `media_0.db` + file-message send (`demo_forward_voice.py`).
- New demos: `demo_group_messages.py` (group + red-packet ZSTD parsing), `demo_robust.py`.

## 🤝 Acknowledgments

Thanks to [vesio](https://github.com/vesio) for sharing the WeChat 4.1.12 UIA control-tree approach and debugging ideas in [issue #1](https://github.com/fanyuantaier/wechatauto-replica/issues/1) — it made the UIA hybrid driver (v1.0.8) possible.

Thanks to [nanshanjack](https://github.com/nanshanjack) for finding the UI-lock re-entrancy problem (fixed in v1.1.2).

Thanks to [maozhitao12450](https://github.com/maozhitao12450) for reporting the WXAM (wxgf) image download issue (fixed in v1.1.3).

Thanks to [uiharukazari0105](https://github.com/uiharukazari0105) for finding that voice data stored in `media_1.db` (and later) was never searched (fixed in v1.1.4).

Thanks to [wenjiavv](https://github.com/wenjiavv) for reporting the missing `threading` import that broke layout calibration in the published 1.2.2.5 ([#28](https://github.com/fanyuantaier/wechatauto-replica/issues/28)) and the substring/no-watermark hole in send verification ([#29](https://github.com/fanyuantaier/wechatauto-replica/issues/29)), both with reproductions and fix proposals (fixed in v1.2.2.6).

## 📄 License & Disclaimer

Apache-2.0. This project is for personal learning and automation research only — please respect the WeChat software license agreement and applicable laws.

Contact: fanyuantaier@163.com
