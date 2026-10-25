# -*- coding: utf-8 -*-
"""V0.6.1：实录敲击剪辑，替换原振荡器合成声。运行仅需Python标准库。"""
from pathlib import Path
import array, hashlib, json, random, struct, wave

ROOT=Path(__file__).resolve().parents[1]
RATE=24000
DURATION=8.2
SOURCE=ROOT/'assets/audio-sources/knocking-on-wood-or-door.wav'
TRIMS=[(2.425,2.640),(2.640,2.845),(2.845,3.300)]
POSITIONS=[4.6,5.15,6.05]
with wave.open(str(SOURCE)) as w:
 assert w.getframerate()==RATE and w.getnchannels()==2 and w.getsampwidth()==2
 pcm=array.array('h',w.readframes(w.getnframes()))
samples=[0.0]*round(RATE*DURATION)*2
rng=random.Random(20261025);low=0.0
for i in range(round(3.35*RATE)):
 t=i/RATE;low=.94*low+.06*rng.uniform(-1,1)
 envelope=max(0,min(1,t/.8,(3.35-t)/.65))
 for c in range(2):samples[i*2+c]=low*.006*envelope

for n,((start,end),position) in enumerate(zip(TRIMS,POSITIONS)):
 clip=pcm[round(start*RATE)*2:round(end*RATE)*2]
 peak=max(abs(x) for x in clip)/32768
 gain=[.64,.59,.66][n]/peak
 frames=len(clip)//2
 for i in range(frames):
  # 保留录音本身的撞击与衰减，仅在切口处做极短淡入淡出。
  fade=min(1,i/(RATE*.001),(frames-1-i)/(RATE*.006))
  for c in range(2):samples[(round(position*RATE)+i)*2+c]+=clip[i*2+c]/32768*gain*max(0,fade)

assert max(abs(x) for x in samples)<1
out=ROOT/'public/intro-knocks.wav'
with wave.open(str(out),'wb') as w:
 w.setnchannels(2);w.setsampwidth(2);w.setframerate(RATE)
 w.writeframes(b''.join(struct.pack('<h',round(x*32767)) for x in samples))
record={'version':'V0.6.1 实录敲击版','asset':'public/intro-knocks.wav','origin':'stephan 的实录敲击，经剪辑与音量平衡；环境底噪由本项目生成','source_title':'Knocking on wood or door','author':'stephan','source_page':'https://commons.wikimedia.org/wiki/File:Knocking_on_wood_or_door.ogg','source_url':'https://upload.wikimedia.org/wikipedia/commons/1/1c/Knocking_on_wood_or_door.ogg','license':'Public domain / PD-author（作者放弃版权并允许任何用途，按来源页面声明）','license_checked_date':'2026-10-25','source_original':'assets/audio-sources/knocking-on-wood-or-door.ogg','source_sha1':'7fae9ee6914c84f37bdf558643a07efc5f7587b0','decoded_source':'assets/audio-sources/knocking-on-wood-or-door.wav','generator':'tools/generate_intro.py','source_trims_seconds':TRIMS,'clip_placement_seconds':POSITIONS,'duration_seconds':DURATION,'sample_rate':RATE,'channels':2,'peak':round(max(abs(x) for x in samples),5),'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'processing':'截取三次真实敲击，平衡音量、轻微切口淡化；未添加振荡器音色、低音增强或人工回声','history':'原合成音效和脚本保留在验收/素材历史目录'}
(ROOT/'开场音效来源V0.6.1.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(record,ensure_ascii=False))
