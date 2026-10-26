# -*- coding: utf-8 -*-
"""Rebalance music; retain one continuous recorded knock sequence, with no reordered hits."""
from pathlib import Path
import array, math, wave, struct, hashlib, json, subprocess, sys
ROOT=Path(__file__).resolve().parents[1]
RATE=24000

def read(path):
    with wave.open(str(path)) as w:
        return w.getframerate(),w.getnchannels(),array.array('h',w.readframes(w.getnframes()))

def write(path,rate,channels,data):
    assert max(abs(x) for x in data)<1
    with wave.open(str(path),'wb') as w:
        w.setframerate(rate);w.setnchannels(channels);w.setsampwidth(2)
        w.writeframes(b''.join(struct.pack('<h',round(x*32767)) for x in data))

# The original music is quiet at -31.7dBFS RMS. Gain +12dB at source,
# leaving substantial headroom and keeping the player's existing volume choice.
subprocess.run([sys.executable,str(ROOT/'tools/generate_ambient.py')],check=True)
rate,channels,music=read(ROOT/'public/ambient.wav')
before_rms=math.sqrt(sum(x*x for x in music)/len(music))/32768/4
music=[x/32768 for x in music]
write(ROOT/'public/ambient.wav',rate,channels,music)

rate,channels,source=read(ROOT/'assets/audio-sources/knocking-on-wood-or-door.wav')
assert rate==RATE and channels==2
start,end,position=.25,1.50,5.15
clip=source[round(start*rate)*2:round(end*rate)*2]
output=[0.0]*round(8.2*rate)*2
# Mild filtering removes close-microphone rumble and brittle top end;
# keep the recording's relative dynamics, timing, and natural decay.
hp=math.exp(-2*math.pi*65/rate)
lp=1-math.exp(-2*math.pi*3000/rate)
for c in range(2):
    prev=0.0;high=0.0;low=0.0
    for i in range(len(clip)//2):
        x=clip[i*2+c]/32768
        high=hp*(high+x-prev);prev=x
        low+=lp*(high-low)
        fade=max(0,min(1,i/(rate*.012),(len(clip)//2-1-i)/(rate*.10)))
        output[(round(position*rate)+i)*2+c]=low*.66*fade
write(ROOT/'public/intro-knocks.wav',rate,channels,output)
(ROOT/'试听').mkdir(exist_ok=True)
write(ROOT/'试听/敲门声V0.6.3.wav',rate,channels,output[round(4.85*rate)*2:round(7.1*rate)*2])
record={
    'version':'V0.6.3','date':'2026-10-26',
    'source_title':'Knocking on Wood or Door','author':'stephan',
    'source_page':'https://commons.wikimedia.org/wiki/File:Knocking_on_wood_or_door.ogg',
    'license':'Public domain / PD-author, verified 2026-10-26',
    'source_clip_seconds':[start,end], 'placement_seconds':position,
    'processing':'连续录音，保留原本节奏和动态；65Hz高通、3kHz低通、整体降低音量；切口淡化；无合成敲击或额外回声',
    'music_gain_db':round(20*math.log10(4),2),
    'music_rms_dbfs_before':round(20*math.log10(before_rms),2),
    'music_rms_dbfs_after':round(20*math.log10(before_rms*4),2),
    'music_peak':round(max(abs(x)for x in music),5),
    'knock_peak':round(max(abs(x)for x in output),5),
    'knock_duration_seconds':8.2,
    'files':{f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in ['public/ambient.wav','public/intro-knocks.wav','试听/敲门声V0.6.3.wav']},
    'perceptual_status':'待用户试听；信号检查不代表真实听感验收'
}
(ROOT/'声音修订来源V0.6.3.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(record,ensure_ascii=False))
