# -*- coding: utf-8 -*-
"""生成当前V0.6.3音乐电平与连续录音开场。"""
from pathlib import Path
import runpy
runpy.run_path(str(Path(__file__).with_name("revise_audio_v063.py")),run_name="__main__")
