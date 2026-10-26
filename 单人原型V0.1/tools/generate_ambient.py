# -*- coding: utf-8 -*-
from pathlib import Path
import math,wave,struct
APP=Path(__file__).resolve().parents[1]
# 本项目原创合成的无词氛围片段；V0.6.3修正电平，播放音量由玩家控制。
rate=16000;length=32;chords=[(130.81,155.56,196.00),(116.54,155.56,174.61),(103.83,130.81,155.56),(116.54,146.83,174.61)]
with wave.open(str(APP/'public/ambient.wav'),'wb') as w:
 w.setnchannels(1);w.setsampwidth(2);w.setframerate(rate)
 buf=bytearray()
 for i in range(rate*length):
  t=i/rate;idx=int(t//8);local=t%8;env=min(1,local/1.8,(8-local)/1.8)
  fade=min(1,t/2,(length-t)/2);value=0
  for f in chords[idx]:value+=math.sin(2*math.pi*f*t)*.026+math.sin(2*math.pi*f/2*t)*.018
  value*=4*max(0,env)*max(0,fade)*(.8+.2*math.sin(t*.4))
  buf.extend(struct.pack('<h',int(max(-1,min(1,value))*32767)))
 w.writeframes(buf)
