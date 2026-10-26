# -*- coding: utf-8 -*-
"""从保留的CC0来源PCM生成当前开锁短音效，不播放音频。"""
from pathlib import Path
import array, math, struct, wave

ROOT=Path(__file__).resolve().parents[1]
source=ROOT/'assets/audio-sources/locking-or-unlocking-door-791845.wav'
with wave.open(str(source)) as w:
    rate=w.getframerate()
    assert rate==24000 and w.getnchannels()==1 and w.getsampwidth()==2
    data=array.array('h',w.readframes(w.getnframes()))
alpha=1-math.exp(-2*math.pi*4200/rate)
low=0.;output=[]
for value in data:
    low+=alpha*(value/32768-low);output.append(low)
peak=max(abs(x) for x in output)
gain=min(1,.30/peak)
for i in range(len(output)):
    fade=max(0,min(1,i/(rate*.004),(len(output)-1-i)/(rate*.035)))
    output[i]*=gain*fade
assert max(abs(x) for x in output)<=.300001
with wave.open(str(ROOT/'public/unlock.wav'),'wb') as w:
    w.setframerate(rate);w.setnchannels(1);w.setsampwidth(2)
    w.writeframes(b''.join(struct.pack('<h',round(x*32767)) for x in output))
