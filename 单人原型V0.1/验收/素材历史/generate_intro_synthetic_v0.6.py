# -*- coding: utf-8 -*-
"""原创合成开场环境声与三次木门敲击；仅使用Python标准库。"""
from pathlib import Path
import math, random, struct, wave, json

ROOT=Path(__file__).resolve().parents[1]
RATE=24000
DURATION=8.2
KNOCKS=[4.6,5.15,6.05]
rng=random.Random(20261025)
low=0.0
samples=[]
for i in range(round(RATE*DURATION)):
 t=i/RATE
 low=.94*low+.06*rng.uniform(-1,1)
 # 底噪淡入后在敲门前退出，留出短暂安静；没有人声和音乐。
 room=max(0,min(1,t/.8,(3.35-t)/.65))
 bed=(low*.012+math.sin(2*math.pi*57*t)*.0013)*room
 channels=[]
 for channel in range(2):
  v=bed
  for n,onset in enumerate(KNOCKS):
   for delay,gain in [(0,1),(.048,.10),(.103,.045)]:
    u=t-onset-delay-channel*.0014
    if not 0<=u<.68:continue
    attack=1-math.exp(-u/0.00065)
    body=sum(a*math.sin(2*math.pi*f*u)*math.exp(-u/decay) for f,a,decay in [(139+n*3,.36,.075),(273-n*4,.18,.043),(467+n*5,.08,.025),(863,.025,.012)])
    tap=rng.uniform(-1,1)*.12*math.exp(-u/.006)
    v+=(body+tap)*attack*gain*[.94,.81,1.0][n]*(1 if channel==0 else .92)
  channels.append(v)
 samples.extend(channels)
peak=max(abs(v) for v in samples)
scale=min(1,.55/peak)
out=ROOT/'public/intro-knocks.wav'
with wave.open(str(out),'wb') as w:
 w.setnchannels(2);w.setsampwidth(2);w.setframerate(RATE)
 w.writeframes(b''.join(struct.pack('<h',round(v*scale*32767)) for v in samples))
record={'asset':'public/intro-knocks.wav','origin':'本项目原创程序合成','generator':'tools/generate_intro.py','duration_seconds':DURATION,'knocks_seconds':KNOCKS,'sample_rate':RATE,'channels':2,'peak':round(peak*scale,5),'rms':round(math.sqrt(sum((v*scale)**2 for v in samples)/len(samples)),5),'rights':'无需第三方音效素材；生成脚本随项目保留','content':'低音量环境底噪、短暂安静、三次带轻微房间反射的木门敲击；无人声，无音乐'}
(ROOT/'开场音效来源V0.6.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(record,ensure_ascii=False))
