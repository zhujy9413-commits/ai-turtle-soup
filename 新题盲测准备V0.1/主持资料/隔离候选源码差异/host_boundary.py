# -*- coding: utf-8 -*-
"""Shared input shapes. Legacy dispatch stays disabled; live_host owns approved dispatch."""
class LiveHostUnavailable(RuntimeError):pass

def public_scope_input(story,history,message):
 return {'public_surface':story['surface'],'public_history':history,'current_message':message}

def private_judge_input(story,history,message,scope,channel='formal_question'):
 if scope not in ['actual','hypothetical']:raise ValueError('范围尚未确定，不能送入汤底判定')
 # The reveal prose duplicates facts in different words. Supply one canonical
 # quotation source to the assessor; keep the literary truth for player reveal.
 fields=['id','title','surface','facts','irrelevant','narration_note','temporal_model']
 # A fact can be answerable without being mandatory for completion. Do not
 # give ordinary yes/no assessment the restoration's grading requirements.
 if channel=='formal_solution':fields+=['nodes','not_required','node_requirements']
 if not story.get('facts'):fields.append('truth')
 # Prior self-grades are not story evidence. Keep them in player/scope history, not the factual assessor.
 context=[item for item in history if item.get('kind') not in ('solution_result','help') and not item.get('superseded')]
 if channel=='formal_solution':
  # Confirmed answers are provided once, in eligible_evidence_sources. Avoid mixing
  # their authoritative progress with old guesses, corrections and host self-grades.
  # One previous restoration remains as referential context, never as completion proof.
  context=[item for item in context if item.get('channel')=='formal_solution'][-1:]
 return {'trusted_channel':channel,'story':{k:story[k] for k in fields if k in story},
  'public_history':context,'current_message':message,'trusted_question_scope':'actual' if channel=='formal_solution' else scope}

def dispatch_provider(*args,**kwargs):
 # No credentials are read and no HTTP request is constructed by this prototype.
 raise LiveHostUnavailable('真实模型尚未启用；需先确认新的调用额度和联调方案。')
