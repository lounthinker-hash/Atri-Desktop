# 在本机运行

## 在 Windows 上启动亚托莉

安装带 pip 的 64 位 Python 3.11 或更新版本。在仓库根目录打开 PowerShell，创建桌面环境。下面使用虚拟环境里的解释器，PowerShell 无需激活脚本。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe tools/check_local_setup.py --desktop
.\启动桌面亚托莉.cmd
```

公开版随附白裙立绘和分层、动作与交互小物、场景与时段图、日记读本及 Windows x64 原生组件。启动器使用刚创建的虚拟环境。角色出现后单击可以打招呼，拖动可以移动，右键可以试演走动和写字、选择场景或打开日记。首次打开日记时没有历史正文。聊天、生成日记及发声还需配置下文的服务。

## 单独查看动作控制器

预览窗口用关节图显示动作控制器。尝试走动后招手、从书写切到倾听，观察脚步、手腕和姿态收势。使用上面建好的环境运行以下命令。有图形桌面时可以交互，也可以单独进行离屏检查。

```powershell
.\.venv\Scripts\python.exe tools/check_local_setup.py --demo
.\.venv\Scripts\python.exe examples/motion_viewer.py --smoke-test
.\.venv\Scripts\python.exe examples/motion_viewer.py
```

## 桌面读取的清单

前五项已经随项目提供。想换成自己有权使用的图或增加动作时，请保持清单中规定的相对路径、图层顺序和锚点。第六项是单独的可选资料。仓库目前没有把零售游戏目录转换成完整音频、脚本索引的公开工具。

| 功能 | 主要入口 | 配套内容 |
| --- | --- | --- |
| 白裙角色与舞台 | `assets/sprites/manifest.json`、`assets/sprites/layers.json`、`data/staging.json` | `d1` 各姿势的图及透明分层、`assets/stage/official-atri.png` |
| 写字、翻页与小物 | `assets/props/manifest.json`、`assets/props/held-diary/actions.json` | 书、执笔手、袖子、书页、中断动作与图集。路径由各自清单解释 |
| 迈步与起跳 | `assets/motion/pose-studies/`、`assets/motion/kick/` | 动作阶段清单、配准图层、姿势几何或局部变形数组 |
| 场景与时段 | `assets/ambience/manifest.json` | `scenes` 下各场景的 `file`、`footAnchor`、`actorHeight`，以及 `variants` 各时段图。教室、海边也有原尺寸背景回退路径 |
| 日记阅读 | `assets/props/diary-reading/manifest.json`、`assets/scenery/bg002.webp` | 日记纸、阅读底板、`handwriting/` 的字体及清单、`stickers/` 的贴纸及清单 |
| 原声和样本对齐 | `assets/voice/manifest.json`、`assets/voice/all.json` | 清单中的音频。样本浏览及其来源标注另用 `tts/original_examples.json` 和 `data/game.js` |

桌面双击启动器也会运行 `--desktop`。它检查 Python 包、本机渲染 DLL、OCR worker、白裙图、日记和场景清单。可选原声索引初始为空。此时点击角色会点头，文字聊天正常呈现回复，日记上的朗读按钮显示“未配置日语语音”。下文的 `--full` 会进一步检查语音环境、模型、原作音频附加资料和 Codex CLI。未满足所选范围内的条件时，自检列出相对路径并以状态码 1 退出。

## Windows 渲染和屏幕识别

仓库内的两个 x64 DLL 和 OCR worker 已经从随附源码编译。需要重编渲染器时，请安装提供 x64 MinGW-w64 的 GCC，例如 MSYS2 的 UCRT64 工具链，让当前 PowerShell 能执行 `gcc.exe`。编译脚本生成 `desktop/renderer/warp_rgba.dll` 和 `desktop/renderer/kick_sampler.dll`。前者用于透明图像变形，后者用于起跳时的姿势过渡。

```powershell
gcc -dumpmachine
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\build_renderer.ps1
```

OCR worker 使用 64 位 .NET Framework 4.x 的 C# 编译器、Windows Runtime 元数据和系统安装的 OCR 语言。安装 NuGet CLI 后，在同一 PowerShell 中获取用于编译的 Windows SDK Contracts。下列版本已用于实际编译。[Microsoft Windows SDK Contracts](https://www.nuget.org/packages/Microsoft.Windows.SDK.Contracts/10.0.22621.2428)

```powershell
$contractsDir = Join-Path $env:TEMP 'atri-sdk-contracts'
nuget install Microsoft.Windows.SDK.Contracts -Version 10.0.22621.2428 -ExcludeVersion -OutputDirectory $contractsDir -Source https://api.nuget.org/v3/index.json
$metadata = Join-Path $contractsDir 'Microsoft.Windows.SDK.Contracts\ref\netstandard2.0'
powershell -NoProfile -ExecutionPolicy Bypass -File .\desktop\observer\build.ps1 -Contracts $metadata
```

输出为 `desktop/observer/ScreenContextWorker.exe`。可以先传入一张自己制作的测试 PNG，检查 Windows OCR 是否能在本机工作。用大字写上 `Build succeeded`，把路径传给 worker，再查看输出的分类与证据字段。桌面运行时，屏幕感知还结合前台应用和窗口信息。媒体播放状态来自 Windows 提供的媒体会话，播放器需要向系统发布播放状态。

```powershell
.\desktop\observer\ScreenContextWorker.exe --fixture-png .\test-screen.png
```

## 本地日语语音

桌面按固定的仓库相对位置查找 `tts/.venv/Scripts/python.exe`。推荐给推理单独准备 Python 3.11 环境。下列 `py -3.11` 依赖 Windows Python Launcher。也可以用本机 Python 3.11 的完整路径。

```powershell
py -3.11 -m venv tts/.venv
.\tts\.venv\Scripts\python.exe -m pip install qwen-tts==0.1.1 soundfile av pyopenjtalk-plus praat-parselmouth
```

根据本机显卡和驱动为这个环境安装对应的 PyTorch。引擎会优先使用可用的 CUDA，否则使用 CPU。CPU 的生成速度取决于机器，第一次合成还要加载模型和日语分词词典。[PyTorch 安装选择器](https://pytorch.org/get-started/locally/)

将完整的模型仓库放到 `tts/models/Qwen3-TTS-12Hz-1.7B-Base/`，保留 `speech_tokenizer/` 和配置文件。引擎从本地加载。若使用 Hugging Face Hub 提供的下载工具，可在此环境中执行以下命令。[Qwen 官方模型与模型卡](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base)

```powershell
.\tts\.venv\Scripts\python.exe tools/fetch_voice_models.py qwen
```

角色语音还读取 `assets/voice/all.json` 中对应风格的本地参考录音和日文文本。自备获准用于该用途的录音，并保留清单中的相对位置。`--voice` 会列出缺失的参考条目、模型文件或 Python 包。桌面的原录音回放可通过 PATH 中的 FFmpeg 或语音环境中的 PyAV 解码。

```powershell
.\tts\.venv\Scripts\python.exe tools/check_local_setup.py --voice
.\tts\.venv\Scripts\python.exe tts/web.py --open
```

第二条会打开本机语音页面。也可以双击 `启动亚托莉语音合成.cmd`。默认服务绑定 `127.0.0.1:8875`，这个端口启用日语输出。访问 `http://127.0.0.1:8875/health` 检查服务，随后在页面中实际合成一句日语，核对参考录音、模型和播放设备是否工作。

## 聊天、日记和总检查

角色聊天和日记生成调用本机已登录的 Codex CLI。桌面会查找 PATH 中的可执行文件，也能查找 Windows 应用自带的 CLI。自检只检查能否发现程序。登录状态、网络和可用额度会影响实际生成。已将 CLI 加到 PATH 的用户可以运行 `codex login status` 确认当前登录状态。

```powershell
.\.venv\Scripts\python.exe tools/check_local_setup.py --full
```

启动完整桌面后，可以依次观察一次走近和停步、一次写字时的打断，打开当天日记，然后让她朗读一段内容。这些操作分别经过实际角色绘制、动作接续、日记存储和语音播放路径。
