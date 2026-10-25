# -*- coding: utf-8 -*-
"""Request-shape preparation only. Dispatch stays disabled until a new approved round."""
class LiveHostUnavailable(RuntimeError):pass

def public_scope_input(story,history,message):
 return {'public_surface':story['surface'],'public_history':history,'current_message':message}

def private_judge_input(story,history,message,scope,channel='formal_question'):
 if scope not in ['actual','hypothetical']:raise ValueError('范围尚未确定，不能送入汤底判定')
 fields=['id','title','surface','truth','facts','nodes','not_required','irrelevant','narration_note','temporal_model']
 return {'trusted_channel':channel,'story':{k:story[k] for k in fields if k in story},
  'public_history':history,'current_message':message,'trusted_question_scope':'actual' if channel=='formal_solution' else scope}

def dispatch_provider(*args,**kwargs):
 # No credentials are read and no HTTP request is constructed by this prototype.
 raise LiveHostUnavailable('真实模型尚未启用；需先确认新的调用额度和联调方案。')
