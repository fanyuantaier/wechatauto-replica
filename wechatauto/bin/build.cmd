@echo off
rem 重建 silkdec.exe —— SILK v3 独立解码器（微信语音 .silk → 24kHz PCM）
rem
rem 前置：
rem   1. clang（mingw-w64 target，如 MSYS2/LLVM；`clang --version` 的 Target 应为
rem      x86_64-w64-windows-gnu）
rem   2. pilk 0.2.4 的 sdist 源码（内置 Silk SDK）：https://pypi.org/project/pilk/
rem      pip download pilk==0.2.4 --no-deps -d . && tar xzf pilk-0.2.4.tar.gz
rem      （需要其中的 src/SKP_SILK_SRC/ 与 src/interface/）
rem
rem 步骤：把本目录的 main_dec.c、skp_assert_stub.c、build.cmd 拷进 pilk-0.2.4 根目录，
rem 然后运行本脚本（会调 clang 编 src/SKP_SILK_SRC/*.c + main_dec.c + 断言桩）。

where clang >nul 2>nul || (echo [!] clang 不在 PATH & exit /b 1)
if not exist src\SKP_SILK_SRC (
  echo [!] 当前目录没有 Silk SDK 源码：先按上方说明下载并解包 pilk-0.2.4，
  echo     再把本目录三个文件拷到 pilk-0.2.4 根目录运行本脚本。
  exit /b 1
)

clang -O2 -Wno-everything -Isrc/SKP_SILK_SRC -Isrc/interface -Isrc/silk ^
  main_dec.c skp_assert_stub.c src/SKP_SILK_SRC/*.c -o silkdec.exe
if %errorlevel%==0 (
  echo [OK] silkdec.exe 已生成，可用 silkdec in.silk out.pcm 24000 解码微信语音
) else (
  echo [!] 构建失败
  exit /b 1
)
