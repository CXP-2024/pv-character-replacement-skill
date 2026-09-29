# 原片分析、二维差值与曝光表

## 分析边界

先人工/辅助划分镜头，选择一个相机与构图可比较的区间，再做绘稿判重。不要把跨镜头或连续相机运动硬聚类成固定人物稿。

使用同一原片统一解码；保存来源指纹、规格、0基帧号及时间。恒帧率可用有理数 `fps` 和帧区间，约定全部 `[start,end)`，end不包含。可变帧率用真实PTS/时长表；随包辅助脚本只处理已确认CFR，不将平均帧率视作VFR曝光。

分析时保留足够全分辨率，眼睑和指尖不应在缩图中丢失。对人物ROI同时看变化像素、最大连贯变化区域、局部网格与边缘结构；细小但语义明确的变化要拆稿。压缩噪声阈值应在同镜头静止持帧与已知轻微动作上校准，宁可多给候选、查阅后合并，不静默漏掉关键变化。

字幕/粒子变化应用明确遮罩排除人物检测，再单独保持它们的原时序。遮罩随镜头变化；不能遮掉头发附近区域来人为降低差值。

## 辅助脚本

依赖 Python、NumPy、OpenCV、Pillow。下列命令中的 `SKILL_DIR` 表示本skill目录，示例 `24/1` 只适用于实际24fps原片。工具不调用生图，也不决定美术验收。

```bash
python SKILL_DIR/scripts/pv_tools.py analyze source.mp4 --start 720 --end 840 --fps 24/1 --cfr --output analysis/shot-01
python SKILL_DIR/scripts/pv_tools.py analyze source.mp4 --start 720 --end 840 --fps 24/1 --cfr --roi 400,100,900,900 --ignore-mask subtitle-mask.png --output analysis/shot-01-masked
python SKILL_DIR/scripts/pv_tools.py diff original-A.png original-B.png --output refs/source-A-B
python SKILL_DIR/scripts/pv_tools.py diff original-A.png original-B.png --roi 400,100,900,900 --output refs/source-A-B-close
python SKILL_DIR/scripts/pv_tools.py validate-timeline timeline.json --report timeline-check.json
```

`--roi` 为 `x,y,width,height`。`--ignore-mask` 与整帧同尺寸，非零像素表示不计入人物判重。先确认掩膜未盖住需要检测的局部。`analyze` 输出候选 `candidates.json` 与代表PNG；查阅各候选及时间边界后才认定真实绘稿。脚本只是一套保守起点，不能检测出所有语义变化。参数选项见 `--help`。

实际压缩PV中，几个主要绘稿可能被保守默认值拆成几十个候选。不要立即为每个候选生成，也不要仅为减少数量而放宽阈值。先找出已知静止持帧的噪声范围，再对跨循环重复稿查阅二维轮廓和关键局部；将复核后合并的映射另存，保留原候选证据。线条加重、局部表情或衣褶确实不同的稿仍保留。

`diff --output` 接受新建或空目录，保存 `comparison.png`、`rgb_absdiff.png`、`contour_overlay.png` 和 `difference.json`，提供真实A/B、放大RGB绝对差值及旧蓝/新橙边缘叠图。不会把两图自动形变对齐，不会把叠图当成新角色绘画。尺寸不同必须先确定是否只是相同画布的不同分辨率；不能拉伸人物来掩盖定位错误。

## 时间轴格式

`assets` 路径相对时间轴文件，下面仅为结构样例，需换为实际存在的角色绘稿。`source_exposures` 来自经查阅的原片；`output_exposures` 是将要采用的角色稿。

```json
{
  "version": 1,
  "fps": "24/1",
  "start": 720,
  "end": 732,
  "mode": "strict_source",
  "source_exposures": [
    {"start": 720, "end": 726, "cel": "original-A"},
    {"start": 726, "end": 732, "cel": "original-B"}
  ],
  "output_exposures": [
    {"start": 720, "end": 726, "cel": "new-A"},
    {"start": 726, "end": 732, "cel": "new-B"}
  ],
  "assets": {
    "new-A": "assets/new-A.png",
    "new-B": "assets/new-B.png"
  }
}
```

两张表必须无空隙、重叠或越界并覆盖同一范围。`strict_source` 中原稿和替换稿使用一致曝光边界、稳定对应关系；角色稿ID可以不同，不能把原来不同的两稿合并成一稿。

`adapted_motion` 可在持帧内加入过渡，保留源表，记录新增稿和时长分配。若原切换点被移动，要检查是否改变节拍；校验器报告边界偏移，不把总长一致当成拍点一致。技术校验也不能判断文件中是否实际上画了相同人物。

## 生图任务与导出

给每张独立绘稿建立任务ID并映射到时间轴；循环复用通过稿，不按文件生成时间排序。任务可有多个候选，但只有采用稿进入 `assets`。源歌词/粒子图层可以每视频帧不同，人物稿的复用不应冻结它们。

合成器由镜头图层和曝光表驱动，不能使用“取最近一张稀疏参考”的隐式匹配。CFR可按完整帧序列或明确重复次数编码；VFR保留PTS与每帧时长。裁切片段的声音起点与视频原时间偏移一致，接回长片不改变整体时长。

修复一段时从最近认可版取其他区间；记录哪些帧由新合成器替换。实际播放、画布/掩膜检查与完整解码检查都通过后再导出最终版。
