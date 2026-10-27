# -*- coding: utf-8 -*-
"""Validate quoted assessment evidence and derive the public verdict in application code."""
import re
from host_feedback import public_references

class AssessmentError(ValueError):
    def __init__(self,code='unsupported_assessment',path=''):
        self.code,self.path=code,path
        super().__init__(code)

def require(test,code='invalid_shape',path=''):
    if not test:raise AssessmentError(code,path)

def criterion_catalog(story):
    result=[]
    for node,text in story['nodes']:
        for i,requirement in enumerate(story.get('node_requirements',{}).get(node,[text]),1):
            result.append({'id':node+'.'+str(i),'node':node,'requirement':requirement})
    return result

def evidence_sources(history,message):
    result={'current':{'text':message['text'],'answer':'','kind':'current'}}
    for key,ref in public_references(history).items():
        if ref['kind']=='confirmed' and ref['answer'] in ('是','否'):
            result[key]={'text':ref['question'],'answer':ref['answer'],'kind':'confirmed'}
    return result

def fact_evidence(items,story):
    require(isinstance(items,list) and 0<len(items)<=8)
    ids=[]
    for item in items:
        require(type(item) is dict and set(item)=={'fact_id','quote'})
        key,quote=item['fact_id'],item['quote']
        require(key in story['facts'],'unknown_fact',str(key))
        require(isinstance(quote,str) and len(quote.strip())>=2 and quote in story['facts'][key],'fact_quote_not_verbatim',key)
        ids.append(key)
    require(len(ids)==len(set(ids)))
    return ids

def text_quote(quote,text,path=''):
    require(isinstance(quote,str) and bool(quote.strip()) and quote in text and len(quote)<=3000,'source_quote_not_verbatim',path)

def solution_quotes(quote,text,path=''):
    """Align conventional ellipsis citations as ordered, exact source fragments.

    This accepts no paraphrase, inserted words, reordering or approximate matches.
    Questions, opposing facts and conflict assertions retain contiguous quoting.
    """
    require(isinstance(quote,str) and bool(quote.strip()) and len(quote)<=3000,'source_quote_not_verbatim',path)
    if quote in text:return [quote]
    pieces=[x.strip() for x in re.split(r'\.{3,}|…+',quote)]
    require(2<=len(pieces)<=4 and all(len(x)>=2 for x in pieces),'source_quote_not_verbatim',path)
    cursor=0
    for piece in pieces:
        start=text.find(piece,cursor)
        require(start>=0,'source_quote_not_verbatim',path)
        cursor=start+len(piece)
    return pieces

def normalize(text):
    # Coverage checks retain predicates/negation but tolerate discourse words outside the quoted clause.
    text=re.sub(r'(^|[，。；,;.])(?:而且|并且|但是|不过|于是|然后|所以|而|但)',r'\1',text)
    text=re.sub(r'[\s，。！？、,.!?“”"‘’：:；;（）()…—\-]','',text)
    text=re.sub(r'^(?:请问|我想问(?:一下)?|我想确认(?:一下)?|我想知道|请帮我确认(?:一下)?|帮我确认(?:一下)?|我的猜测是|我的意思是|我认为|我觉得|我猜|也就是说|所以说|是不是说)','',text)
    text=re.sub(r'(?:对不对|对吗|是吗|这样理解对吗|是不是这样)$','',text)
    return re.sub(r'[吗呢吧呀啊]+$','',text)

def full_coverage(text,quotes):
    target=normalize(text);require(bool(target))
    covered=[False]*len(target)
    for quote in quotes:
        value=normalize(quote);require(bool(value))
        start=target.find(value)
        require(start>=0)
        while start>=0:
            for i in range(start,start+len(value)):covered[i]=True
            start=target.find(value,start+1)
    require(all(covered),'current_claim_not_fully_covered')

def question_verdict(value,story,message,templates):
    require(type(value) is dict and set(value)=={'mode','claims','clarification'})
    mode=value['mode'];require(mode in ('assess','clarify','invalid','decline','content_gap'))
    require(isinstance(value['claims'],list) and len(value['claims'])<=10)
    result={'action':mode,'answer':'','clarification':'','fact_ids':[],'covered_nodes':[],
            'review_refs':[],'audit_flags':[],'negative_claims':[]}
    # Populated claims are assessed below in full. An inconsistent routing label
    # cannot overrule their verified evidence; no model answer/action is trusted.
    if value['claims'] and mode!='assess':
        require(value['clarification']=='','conflicting_clarification')
        # A contradictory routing label is recoverable only when every claim
        # has a fixed-fact relation to validate. Ungrounded unrelated/unknown
        # labels cannot turn a malformed decline into a published answer.
        require(all(type(c) is dict and c.get('status') in ('supported','contradicted') for c in value['claims']),
                'ungrounded_mode_conflict','claims')
        result['audit_flags'].append('model_mode_conflict');mode='assess'
    if mode!='assess':
        require(not value['claims'])
        if mode=='clarify':
            require(value['clarification'] in templates.values());result['clarification']=value['clarification']
        else:require(value['clarification']=='')
        if mode=='content_gap':result.update(answer='不清楚',audit_flags=['core_gap'])
        return result
    require(value['clarification']=='' and value['claims'])
    statuses=[];quotes=[]
    for claim in value['claims']:
        require(type(claim) is dict and set(claim)=={'quote','status','facts'})
        quote,status=claim['quote'],claim['status'];text_quote(quote,message['text'])
        require(status in ('supported','contradicted','unrelated','unknown'))
        if status in ('supported','contradicted'):
            ids=fact_evidence(claim['facts'],story);result['fact_ids']+=ids
        else:require(claim['facts']==[])
        if status=='contradicted':result['negative_claims'].append(claim)
        statuses.append(status);quotes.append(quote)
    full_coverage(message['text'],quotes)
    result['fact_ids']=list(dict.fromkeys(result['fact_ids']))
    # No model-supplied yes/no field exists. All current clauses must be assessed.
    if 'contradicted' in statuses:answer='否'
    elif 'unknown' in statuses:answer='不清楚'
    elif all(x=='unrelated' for x in statuses):answer='无关'
    elif 'unrelated' in statuses:result['action']='invalid';return result
    else:answer='是'
    result.update(action='answer',answer=answer)
    return result

def solution_verdict(value,story,history,message):
    require(type(value) is dict and set(value)=={'mode','checks','conflicts','clarification','review_refs'})
    mode=value['mode'];require(mode in ('assess','clarify','invalid','decline','content_gap'))
    require(isinstance(value['checks'],list) and isinstance(value['conflicts'],list))
    refs=public_references(history)
    selected=value['review_refs'];require(isinstance(selected,list) and len(selected)<=2 and len(selected)==len(set(selected)) and all(x in refs for x in selected),'invalid_public_reference','review_refs')
    result={'action':mode,'answer':'','clarification':value['clarification'],'fact_ids':[],
            'covered_nodes':[],'review_refs':selected,'audit_flags':[],'negative_claims':[],
            'missing_criteria':[],'current_evidence':[],'aligned_evidence':[],'rejected_evidence':[]}
    if value['checks'] and mode!='assess':
        require(value['clarification']=='','conflicting_clarification')
        result['audit_flags'].append('model_mode_conflict');mode='assess'
    if mode!='assess':
        require(not value['checks'] and not value['conflicts'] and not selected)
        if mode=='content_gap':result.update(answer='不清楚',audit_flags=['core_gap'])
        return result
    require(value['clarification']=='')
    # A substantiated contradiction already precludes completion. The absence of
    # other progress checks is ungraded, not missing and not an integration error.
    if not value['checks'] and value['conflicts']:
        require(len(value['conflicts'])<=3)
        for conflict in value['conflicts']:
            require(type(conflict) is dict and set(conflict)=={'quote','facts'})
            text_quote(conflict['quote'],message['text'])
            result['fact_ids']+=fact_evidence(conflict['facts'],story)
            result['negative_claims'].append(conflict)
        result['fact_ids']=list(dict.fromkeys(result['fact_ids']))
        result.update(action='conflict',progress_assessed=False)
        return result
    catalog=criterion_catalog(story);expected={x['id'] for x in catalog}
    require(len(value['checks'])==len(expected) and {x.get('criterion_id') for x in value['checks']}==expected,'criterion_set_mismatch','checks')
    sources=evidence_sources(history,message);met=set()
    for check in value['checks']:
        require(type(check) is dict and set(check)=={'criterion_id','state','evidence'})
        require(check['state'] in ('met','missing') and isinstance(check['evidence'],list) and len(check['evidence'])<=4)
        key=check['criterion_id']
        if check['state']=='missing':
            require(not check['evidence'],'missing_condition_has_evidence',key);result['missing_criteria'].append(key);continue
        require(bool(check['evidence']),'met_condition_lacks_evidence',key)
        usable=0
        for evidence in check['evidence']:
            require(type(evidence) is dict and set(evidence)=={'source_id','quote'})
            try:
                source=sources.get(evidence['source_id']);require(source is not None,'unknown_evidence_source',key)
                pieces=solution_quotes(evidence['quote'],source['text'],key+'/'+evidence['source_id'])
            except AssessmentError as error:
                # Multiple citations are alternatives, not separate required claims.
                # A bad duplicate is never used as evidence. Every met condition
                # must still retain at least one exact eligible citation below.
                result['rejected_evidence'].append({'criterion_id':key,**evidence,'reason':error.code})
                continue
            usable+=1
            result['aligned_evidence'].append({'criterion_id':key,'source_id':evidence['source_id'],'quotes':pieces})
            if evidence['source_id']=='current':result['current_evidence']+=pieces
        require(usable>0,'met_condition_lacks_verifiable_evidence',key)
        met.add(key)
    if result['rejected_evidence']:result['audit_flags'].append('unusable_citation_ignored')
    # An actual contradiction needs a current player span AND a quoted opposing fixed fact.
    require(len(value['conflicts'])<=3)
    for conflict in value['conflicts']:
        require(type(conflict) is dict and set(conflict)=={'quote','facts'})
        text_quote(conflict['quote'],message['text'])
        result['fact_ids']+=fact_evidence(conflict['facts'],story)
        result['negative_claims'].append(conflict)
    for node,_ in story['nodes']:
        required={x['id'] for x in catalog if x['node']==node}
        if required<=met:result['covered_nodes'].append(node)
    result['fact_ids']=list(dict.fromkeys(result['fact_ids']))
    result['missing_criteria']=[x['id'] for x in catalog if x['id'] not in met]
    result['action']='conflict' if value['conflicts'] else 'incomplete' if result['missing_criteria'] else 'complete'
    return result

def is_negative(verdict):
    return verdict['action']=='conflict' or (verdict['action']=='answer' and verdict['answer']=='否')
