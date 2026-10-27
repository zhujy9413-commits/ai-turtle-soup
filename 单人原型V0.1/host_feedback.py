# -*- coding: utf-8 -*-
"""Player-facing help assembled exclusively from already public records."""
import re

def effective_answers(messages):
    latest={}
    for item in messages:
        if item.get('role')!='host' or item.get('kind') not in ('answer','correction') or item.get('corrected_by'):continue
        answer=item.get('answer',item.get('text'))
        if item.get('question_id') and answer in ('是','否','无关','不清楚'):
            latest[item['question_id']]={**item,'text':answer}
    return [latest[q['id']] for q in messages if q.get('role')=='player' and q.get('kind')=='question' and q['id'] in latest]

def local_intent(text):
    value=re.sub(r'[\s，。！？、,.!?“”"：:；;]','',text).strip()
    if re.fullmatch(r'(?:请|帮我|能不能|可以|那|所以)?(?:说说|告诉我)?(?:哪里|哪儿|哪部分|哪些地方|还有哪里|还差哪里)(?:还|尚|尚未|没|没有|不|未)?(?:还原)?(?:完整|完全|盘完|还原完整|还没完整|不完整)',value):
        return 'progress'
    if re.fullmatch(r'(?:我的|刚才的|这个|这次的|这段)?(?:还原|推理|答案)?(?:还缺什么|差在哪|缺在哪|哪里不对|哪里有问题|为什么没通过|为什么不通过|为啥不通过|还差什么|还漏了什么|少了什么)',value):
        return 'progress'
    if re.fullmatch(r'(?:请|给我|来个|再给我|帮我)?(?:一个|一点|个|下一层)?提示(?:一下|吧|好吗|可以吗)?',value):
        return 'hint'
    if re.fullmatch(r'(?:这个游戏|这局|海龟汤)?(?:怎么玩|规则是什么|怎么提交还原|怎么提问|如何提问)',value):
        return 'rules'
    return None

def public_references(history):
    """IDs point only to actual answers with their public questions, or published hints."""
    questions={x.get('id'):x for x in history if x.get('role')=='player' and x.get('channel')=='formal_question'}
    refs={};latest={}
    for item in history:
        if item.get('superseded'):continue
        key=item.get('id')
        if not key:continue
        if item.get('kind')=='hint':
            refs[key]={'id':key,'kind':'hint','text':item['text']}
        elif item.get('kind') in ('answer','correction') and item.get('scope','actual')=='actual':
            question=questions.get(item.get('question_id'))
            answer=item.get('answer',item['text'])
            if question and answer in ('是','否','无关','不清楚'):
                previous=latest.get(item.get('question_id'))
                if previous:refs.pop(previous,None)
                refs[key]={'id':key,'kind':'confirmed','question':question['text'],'answer':answer}
                latest[item.get('question_id')]=key
    return refs

def solution_feedback(action, refs, selected, assessment=None, story=None):
    if action=='complete':return '还原成立。'
    if assessment and story:
        if action=='conflict' and assessment.get('negative_claims'):
            quote=assessment['negative_claims'][0]['quote']
            public=[]
            for key in selected[:2]:
                ref=refs.get(key)
                if not ref:continue
                public.append('已公开提示：“'+ref['text']+'”' if ref['kind']=='hint' else '“'+ref['question']+'”——此前回答：'+ref['answer']+'。')
            return ('先核对你这次写的这一句：“'+quote+'”。\n这句话与固定设定不符。'+
                    ('可对照这条已公开信息：\n'+'\n'.join(public) if public else '可以先把这一点单独提问。')+'\n其它已确认的部分保留。')
        for key in assessment.get('missing_criteria',[]):
            focus=story.get('progress_questions',{}).get(key)
            if not focus or not all(x in story['surface'] for x in focus.get('requires_surface',[])):continue
            current=assessment.get('current_evidence',[])
            lead=('你已经说明了“'+current[0][:75]+('…' if len(current[0])>75 else '')+'”。\n') if current else ''
            return lead+'还需要补上这一条关系：'+focus['question']+'\n已确认的内容不用重写，把这条关系补进还原即可。'
    intro='这段还原与已确认的记录存在冲突。请先核对：' if action=='conflict' else '还原中的因果还没有连完整。可以先回到这条已公开的信息：'
    chosen=[refs[key] for key in selected[:2] if key in refs]
    if not chosen:
        return ('这段还原与已确认的记录存在冲突。先核对此前的回答，再调整解释；可以问我“哪里不完整”。' if action=='conflict' else
                '还原中的因果还没有连完整。把汤面里的反常行为与已确认的原因连起来；可以问我“哪里不完整”，或回看已公开提示。')
    lines=[]
    for item in chosen:
        lines.append('已公开提示：“'+item['text']+'”' if item['kind']=='hint' else
                     '“'+item['question']+'”——此前回答：'+item['answer']+'。')
    return intro+'\n'+'\n'.join(lines)+'\n把这条信息与还原里的原因、经过连起来即可，不必重复无关细节。'

def help_text(history, kind='progress'):
    if kind=='rules':
        return '用一个是非问题验证猜测；形成解释后点击“提交还原”。三层提示可主动领取，澄清与对局求助不扣次数。'
    last=next((x for x in reversed(history) if x.get('kind')=='solution_result'),None)
    if last and ('还需要补上这一条关系：' in last['text'] or '先核对你这次写的这一句：' in last['text']):
        return last['text']
    if last and ('此前回答：' in last['text'] or '已公开提示：' in last['text']):
        return '先看上一段还原反馈里的已知线索：\n'+last['text']
    refs=public_references(history)
    if refs:
        latest=list(refs)[-1]
        return solution_feedback('incomplete',refs,[latest])
    return '先把你已确认的经过、原因和汤面中的反常行为连起来。对局求助不会扣次数；想获得新线索时，可以主动领取一层提示。'
