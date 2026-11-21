# wechatauto/bin —— 语音解码器 silkdec

`silkdec.exe`：微信语音 SILK v3 → 16bit PCM（默认 24kHz 单声道）的独立解码器，
用于把 `VoiceInfo.voice_data` 落盘的 `.silk`（微信变体，`\x02#!SILK_V3`）转成可听/可转写的
WAV（WAV 封装由 `MediaDownloader.silk_to_wav` 用标准库 `wave` 完成）。

## 用法

- 代码：`MediaDownloader.download_voice(user, local_id, to_wav=True)`（自动找本目录的
  `silkdec.exe`）；或手动 `MediaDownloader.silk_to_wav(silk_path)`。
- 解码器查找顺序：环境变量 `WECHATAUTO_SILKDEC` → 包内 `wechatauto/bin/silkdec.exe` → PATH。
- 手动：`silkdec in.silk out.pcm 24000`

## 二进制信息

- 来源：pilk 0.2.4（PyPI，MIT）内置的 Silk SDK（Broadcom/Google SILK v3，随 SDK 许可证）
  解码循环，抽出为不依赖 Python 的 `main_dec.c`（见本目录）。
- 本机构建：clang 22.1.8（Target `x86_64-w64-windows-gnu`），详见 `build.cmd`。
- sha256：`DA0FBB2D09F72268034498E5BA80006D4ACB9376A97EC88AD7D507AD27A283A6`
- 兼容流头：微信变体 `02 23 21 53 49 4c 4b 5f 56 33`（`\x02#!SILK_V3`）与裸 `#!SILK_V3`。
- 说明：ffmpeg 8.x 无法解裸 `.silk` 流（其 SILK 解码器仅用于容器内引用），所以需要本独立解码器。

## 重建

见 `build.cmd` 顶部注释（需要 clang 与 pilk 0.2.4 sdist 源码；`main_dec.c` / `skp_assert_stub.c`
为本库维护的改动：前者把 pilk 的 Python 绑定解码循环改为独立 main()，后者补 `SKP_assert` 桩）。
