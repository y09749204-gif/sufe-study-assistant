"use client";
import {useState} from "react";
import {fetchJson} from "./api";

export type ClassroomRequirement={id:string;status?:string;payload:{notice_kind:string;raw_text:string;course_name?:string;recording_title?:string;evidence?:string;evidences?:{text:string;timestamp_seconds:number|null}[];timestamp_seconds?:number|null;audience?:string;time_text?:string;deadline_at?:string|null;applicable_at?:string|null;time_status?:string;needs_verification?:boolean;verification_note?:string;possible_duplicate_ids?:string[];effect?:{task_id?:string};edit_history?:unknown[];source?:string}};
const kinds:Record<string,string>={assignment:"作业",reading:"阅读",collaboration:"协作与展示",classroom:"课堂要求",schedule:"调课"};
const stamp=(s:number|null|undefined)=>s==null?"时间未知":`${Math.floor(s/60)}:${Math.floor(s%60).toString().padStart(2,"0")}`;
function inputTime(value?:string|null){if(!value)return "";const date=new Date(value);return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16)}

export function ClassroomRequirementCard({item,onResolve,onChanged}:{item:ClassroomRequirement;onResolve:(id:string,action:"accept"|"reject")=>Promise<void>;onChanged?:()=>Promise<void>}){
 const p=item.payload;const [editing,setEditing]=useState(false);const [busy,setBusy]=useState(false);const [error,setError]=useState("");const [draft,setDraft]=useState({raw_text:p.raw_text,audience:p.audience||"",time_text:p.time_text||"",deadline_at:inputTime(p.deadline_at),applicable_at:inputTime(p.applicable_at)});
 const pending=!item.status||item.status==="pending";
 async function act(fn:()=>Promise<unknown>){setBusy(true);setError("");try{await fn()}catch(e){setError(String(e))}finally{setBusy(false)}}
 async function save(){await fetchJson(`/api/academics/review/${item.id}`,{method:"PATCH",body:JSON.stringify({...draft,deadline_at:draft.deadline_at?new Date(draft.deadline_at).toISOString():null,applicable_at:draft.applicable_at?new Date(draft.applicable_at).toISOString():null})});setEditing(false);await onChanged?.()}
 const times=[p.deadline_at,p.applicable_at].filter(Boolean) as string[];
 return <article className="lesson-requirement" data-requirement-id={item.id}><small>{kinds[p.notice_kind]||p.notice_kind} · {pending?"待确认":item.status==="accepted"?"已确认":"已忽略"} · {stamp(p.timestamp_seconds)}</small><strong style={{display:"block"}}>{p.course_name} {p.recording_title&&p.recording_title!==p.course_name?`· ${p.recording_title}`:""}</strong>
 {editing?<div className="requirement-editor"><label>具体行动<textarea aria-label="具体行动" value={draft.raw_text} onChange={e=>setDraft({...draft,raw_text:e.target.value})}/></label><label>适用对象<input aria-label="适用对象" value={draft.audience} onChange={e=>setDraft({...draft,audience:e.target.value})}/></label><label>原文时间要求<input aria-label="原文时间要求" value={draft.time_text} onChange={e=>setDraft({...draft,time_text:e.target.value})}/></label><label>截止时间<input aria-label="截止时间" type="datetime-local" value={draft.deadline_at} onChange={e=>setDraft({...draft,deadline_at:e.target.value})}/></label><label>适用时间（如上课时间）<input aria-label="适用时间" type="datetime-local" value={draft.applicable_at} onChange={e=>setDraft({...draft,applicable_at:e.target.value})}/></label><button disabled={busy||!draft.raw_text.trim()} onClick={()=>void act(save)}>保存修改</button><button disabled={busy} onClick={()=>setEditing(false)}>取消</button></div>:<><p style={{whiteSpace:"pre-wrap"}}>{p.raw_text}</p><p>适用对象：{p.audience||"原记录未注明"}<br/>时间要求：{p.time_text||"未注明"}</p>{p.deadline_at&&<p>截止：{new Date(p.deadline_at).toLocaleString("zh-CN")}</p>}{p.applicable_at&&<p>适用：{new Date(p.applicable_at).toLocaleString("zh-CN")}</p>}</>}
 {times.some(t=>new Date(t).getTime()<Date.now())&&<p className="notice">原要求时间已过去，请核实是否仍需处理；不会自动顺延。</p>}
 {(p.needs_verification||p.time_status==="needs_verification")&&<p className="notice">待核实：{p.verification_note||"时间或适用范围尚不明确，请核对原文。"}</p>}
 {!!p.possible_duplicate_ids?.length&&<p className="notice">其他课次出现相似要求，请核对是否已经完成。</p>}
 <details><summary>课堂原文依据</summary>{(p.evidences?.length?p.evidences:[{text:p.evidence||p.raw_text,timestamp_seconds:p.timestamp_seconds??null}]).map((e,i)=><blockquote key={i}><small>{stamp(e.timestamp_seconds)}</small><p style={{whiteSpace:"pre-wrap"}}>{e.text}</p></blockquote>)}</details>
 {!!p.edit_history?.length&&<details><summary>校订记录（{p.edit_history.length}）</summary><pre style={{whiteSpace:"pre-wrap"}}>{JSON.stringify(p.edit_history,null,2)}</pre></details>}
 {pending&&!editing&&<div className="candidate-actions"><button disabled={busy} onClick={()=>{setDraft({raw_text:p.raw_text,audience:p.audience||"",time_text:p.time_text||"",deadline_at:inputTime(p.deadline_at),applicable_at:inputTime(p.applicable_at)});setEditing(true)}}>修改</button><button disabled={busy} onClick={()=>void act(()=>onResolve(item.id,"accept"))}>{p.notice_kind==="schedule"?"确认":"确认生成待办"}</button><button disabled={busy} onClick={()=>void act(()=>onResolve(item.id,"reject"))}>忽略</button></div>}
 {p.effect?.task_id&&<a href={`/?view=inbox#task-${p.effect.task_id}`}>查看对应待办</a>}{error&&<p role="alert">{error}</p>}
 </article>
}
