# PV Character Replacement Skill

保留原 PV 的手绘画风、分镜和表演，把人物替换为自己的角色或 OC。面向低帧率帧动画、交替绘稿和角色重绘，不是普通视频换脸工具。

这份 skill 从实际 PV 制作与多轮修复中提炼：眼睛/眉毛和手部结构、过度精细的画风、独立粒子遗漏、框内背景误删、红发被误认成场景、交替稿过度跳变或过于静止，以及长发转向的连续性。

## 包含什么

- [SKILL.md](skills/pv-character-replacement/SKILL.md)：制作约定、参考图职责、生成顺序、连续性与交付。
- [Prompt 模板](skills/pv-character-replacement/references/prompt-patterns.md)：画风锚帧、真实二维差值迁移、相邻端点过渡及局部修复。
- [分析与曝光](skills/pv-character-replacement/references/analysis-and-timing.md)：严格关键稿识别、源时间轴与替换时间轴。
- [图层与连续性](skills/pv-character-replacement/references/layers-and-continuity.md)：语义区域、掩膜、长发/衣物风动与循环衔接。
- [检查与交付](skills/pv-character-replacement/references/review-and-delivery.md)：逐稿、连续组、原速对比和增量修复。
- [pv_tools.py](skills/pv-character-replacement/scripts/pv_tools.py)：CFR候选绘稿分析、二维差值图、时间轴校验。

生图由宿主的图像生成工具完成。辅助脚本不调用付费API、不包含模型凭据，也不通过光流/网格形变制作人物动作。模型仍可能忽略提示中的位置和结构，需要查阅生成结果及原速播放。

## 安装

把 `skills/pv-character-replacement` 整个目录放进宿主的技能目录。对于使用 `~/.codex/skills` 的环境：

```bash
git clone https://github.com/CXP-2024/pv-character-replacement-skill.git
```

Windows PowerShell：

```powershell
$skillTarget = Join-Path $env:USERPROFILE '.codex/skills/pv-character-replacement'
if (Test-Path -LiteralPath $skillTarget) { throw 'Skill already exists; compare or back it up before updating.' }
Copy-Item -Recurse -LiteralPath './pv-character-replacement-skill/skills/pv-character-replacement' -Destination $skillTarget
```

macOS / Linux（已有同名skill时先对比或备份）：

```bash
mkdir -p ~/.codex/skills
cp -R pv-character-replacement-skill/skills/pv-character-replacement ~/.codex/skills/
```

若设置了自定义技能目录，改用该目录。确保宿主能够发现新skill后，可这样调用：

> 使用 $pv-character-replacement，把这段 PV 的人物替换成我的角色。保留原画风和分镜，先分析独立绘稿并做 5–10 秒左右对比样片。

修复时可写：

> 使用 $pv-character-replacement，修复 32–35 秒背影。参考原作头发和衣袖的风动；允许生图补绘转向过渡，保持镜头总长，核对整个循环。

## 辅助脚本

Python 3.10+，脚本依赖见 [requirements.txt](requirements.txt)。建议在独立虚拟环境安装：

```bash
python -m pip install -r requirements.txt
python skills/pv-character-replacement/scripts/pv_tools.py --help
python -m unittest discover -s tests -v
```

`analyze` 要求明确声明原片为CFR并提供有理数帧率；它产生待查阅的候选，不自动宣称通过。`diff` 提供真正的二维差值和轮廓叠图，标量分数只用于筛选。`validate-timeline` 检查曝光覆盖、资源和模式约束，不能判断美术正确性。

默认 `strict_source` 保留原稿曝光；`adapted_motion` 在用户需要过渡时调整持帧，保留总时长并记录变化。这两种模式不能同时承诺逐帧完全一致。

仓库只含通用skill、脚本与合成测试；没有原PV、角色参考、生成媒体或本地项目缓存。
