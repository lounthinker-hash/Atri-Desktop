# 安装本地语音模型

桌面朗读用 Qwen3-TTS 合成新的日语语音。RVC 是单独的变声实验，可以用自己的录音训练，然后转换已有 WAV。两套模型可以分别安装。

## 从官方模型库安装

在项目根目录用 Python 3.11 或更高版本运行。下载脚本只依赖 Python 标准库。

```powershell
python tools/fetch_voice_models.py qwen
```

需要训练变声器时，将 `qwen` 换成 `rvc`。要安装两套，可使用 `all`。脚本从固定的上游修订下载，逐个比对字节数和 SHA-256，验证通过才替换目标文件。网络中断后重跑同一命令即可继续，已校验的文件会跳过。可以用 `--root <目录>` 指向另一份项目。校验值见[模型清单](../tools/voice-models.lock.json)。

| 模型 | 用途 | 仓库内随附 | 还需下载 |
| --- | --- | --- | --- |
| Qwen3-TTS-12Hz-1.7B-Base | 桌面日语合成 | 配置、词表、分词规则 | 主权重与语音 tokenizer 共 4.54 GB |
| RVC v2 40 kHz | 可选的本地训练、音色转换 | 通用生成器 `f0G40k.pth`（69.72 MiB）与 HuBERT 配置 | HuBERT 内容编码器和训练判别器共 332.08 MB |

Qwen 放在 `tts/models/Qwen3-TTS-12Hz-1.7B-Base/`，RVC 放在 `tts/voice_lab/assets/`。运行下列命令可离线校验全部 16 个运行文件。只安装一组时，把 `all` 换成对应名称。

```powershell
python tools/fetch_voice_models.py all --verify-only
```

Qwen 引擎还需要匹配的 Python 环境和可用于合成的参考录音及日文转写。原声索引和语音服务的检查方法见[本地安装说明](local-setup.md#本地日语语音)。

## 从 GitHub Release 离线安装

在一台能联网的电脑上，从语音模型发行页把 `Atri-voice-models.7z.001` 起的全部分卷、`SHA256SUMS` 和 `MODEL-BUNDLE.txt` 下载到同一目录。可以把整个目录拷贝到离线电脑。[语音模型发行页](https://github.com/lounthinker/Atri-Desktop/releases/tag/voice-models-2026-11)

先用 PowerShell 的 `Get-FileHash -Algorithm SHA256` 将各分卷的结果与 `SHA256SUMS` 逐项比对。然后在项目根目录用 7-Zip 解开第一卷，最后检查模型文件。

```powershell
7z x C:\Downloads\Atri-models\Atri-voice-models.7z.001
python tools/fetch_voice_models.py all --verify-only
```

## 用自己的录音训练 RVC

实验脚本位于 `tts/voice_lab/`。特征提取和音频转换可以使用 CPU；训练脚本使用支持 bfloat16 的 CUDA 显卡。在语音环境中按本机驱动安装 CUDA 版 PyTorch，另装 Transformers、NumPy、SciPy、soundfile、soxr、librosa 和 praat-parselmouth。项目使用的 Transformers 版本为 4.57.3。[PyTorch 安装选择器](https://pytorch.org/get-started/locally/)

```powershell
.\tts\.venv\Scripts\python.exe -m pip install transformers==4.57.3 numpy scipy soundfile soxr librosa praat-parselmouth
python tools/fetch_voice_models.py rvc
```

用自己有权训练的录音建立 UTF-8 JSON 清单。例如，以下条目对应清单旁边的 `audio/greeting01.wav`。把文字和时长换成录音的实际内容。脚本按日文文本去重，从每个场景抽取时长在 1.3 到 8.6 秒之间、规范化文本至少四个字符的条目。

```json
[
  {
    "id": "greeting01",
    "file": "audio/greeting01.wav",
    "ja": "おはようございます。",
    "scene": "greetings",
    "duration": 2.4
  }
]
```

以下命令依次提取特征、微调、构建检索索引、转换输入音频。如果希望保留某条录音作对照，可在第一条命令后加 `--holdout-id <录音ID>`。同一转写的其他录音也会留出。挑选样本时可加 `--select-only`，先检查本地 `data/split.json`。

```powershell
$voicePython = ".\tts\.venv\Scripts\python.exe"
& $voicePython tts/voice_lab/prepare.py --catalog .\my-recordings\catalog.json --device cpu
& $voicePython tts/voice_lab/train_atri.py --steps 450 --batch 4 --save-every 100
& $voicePython tts/voice_lab/convert.py --build-index
& $voicePython tts/voice_lab/convert.py .\input.wav .\converted.wav --device cpu
```

训练特征、训练出来的说话人权重和检索索引保存在本地 `tts/voice_lab/data/` 与 `tts/voice_lab/checkpoints/`。换数据集时使用新的项目目录，避免复用同名录音的旧特征。转换默认加载本地训练步数最高的检查点，输出 40 kHz 单声道 PCM WAV。`--retrieval 0` 可关闭检索，`--checkpoint <文件>` 可选择自己训练中的某一步。

## 上游与许可

- Qwen 模型固定为 `fd4b254389122332181a7c3db7f27e918eec64e3`，模型卡标注 Apache-2.0。随附分词与配置文件保留上游字节。[固定模型快照](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base/tree/fd4b254389122332181a7c3db7f27e918eec64e3) · [随附许可](../tts/models/Qwen3-TTS-12Hz-1.7B-Base/LICENSE)
- RVC 基础权重使用 `lj1995/VoiceConversionWebUI` 的 `e6d0c1a17da07c33557852f9dfa2bd44cc75737d`，模型库标注 MIT。实验依赖的 RVC 上游源码取自 `8f2fdbf483955f924b4c87ab34919170d0b704ed`，保留其作者署名和许可。[固定权重快照](https://huggingface.co/lj1995/VoiceConversionWebUI/tree/e6d0c1a17da07c33557852f9dfa2bd44cc75737d) · [上游源码](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI/tree/8f2fdbf483955f924b4c87ab34919170d0b704ed) · [随附许可](../tts/voice_lab/rvc/LICENSE)
