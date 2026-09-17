"use client";
import {useState} from 'react';
import {fetchJson} from './api';

const presets: Record<string, {label:string; provider:string; base_url:string}> = {
  none: {label:'暂不使用 AI', provider:'none', base_url:''},
  ollama: {label:'本机 Ollama', provider:'ollama', base_url:'http://127.0.0.1:11434'},
  openai: {label:'OpenAI', provider:'openai', base_url:'https://api.openai.com/v1'},
  deepseek: {label:'DeepSeek', provider:'openai', base_url:'https://api.deepseek.com'},
  custom: {label:'其他 OpenAI 兼容 API', provider:'openai', base_url:''},
};
export const clearAISecrets = (value:any) => ({...value, api_key:'', ...(value.vision_service ? {vision_service:{...value.vision_service, api_key:''}} : {})});

export default function AISettings({value, onChange, onSaved}: {value:any; onChange:(value:any)=>void; onSaved?:()=>Promise<unknown>}) {
  const [busy,setBusy] = useState(false);
  const [notice,setNotice] = useState('');
  const [models,setModels] = useState<Record<string,string[]>>({});
  const [verified,setVerified] = useState<Record<string,string>>({});
  const reuse = value.vision_reuse !== false;
  const vision = reuse ? value : (value.vision_service || {provider:'none',base_url:'',vision_model:''});
  const fingerprint = (role:string) => JSON.stringify(role==='text' ? value : vision);
  function change(role:string, patch:any) {
    setVerified({}); setNotice('配置已修改，请保存并重新测试。');
    if (role==='vision' && !reuse) onChange({...value, vision_service:{...vision,...patch}});
    else onChange({...value,...patch});
  }
  async function save() {
    await fetchJson('/api/setup/ai',{method:'PUT',body:JSON.stringify(value)});
    const status:any = await fetchJson('/api/setup');
    const saved = clearAISecrets(status.settings.ai);
    onChange(saved);
    await onSaved?.();
    return saved;
  }
  async function run(task:()=>Promise<void>) {
    setBusy(true); setNotice('正在处理…');
    try {await task();} catch(e) {setNotice(e instanceof Error ? e.message : '操作失败');}
    finally {setBusy(false);}
  }
  function service(role:string, cfg:any) {
    const vendor = cfg.provider==='none' || !cfg.provider ? 'none' : cfg.provider==='ollama' ? 'ollama' : cfg.vendor && cfg.vendor!=='none' ? cfg.vendor : 'custom';
    return <>
      <label>{role==='text'?'文字服务':'看图服务'}提供方<select aria-label={role==='text'?'AI 提供方':'看图服务提供方'} value={vendor} onChange={e=>{const preset=presets[e.target.value];setModels({});change(role,{...preset,vendor:e.target.value,api_key:'',api_key_set:false,cloud_consent:false,text_model:'',vision_model:''});}}>
        {Object.entries(presets).map(([id,p])=><option key={id} value={id}>{p.label}</option>)}
      </select></label>
      {cfg.provider && cfg.provider!=='none' && <>
        <label>API 服务地址<input value={cfg.base_url||''} placeholder="https://服务商地址/v1" onChange={e=>{setModels({});change(role,{base_url:e.target.value,api_key:'',api_key_set:false,cloud_consent:false});}}/></label>
        {cfg.provider==='openai' && <>
          <label>API Key<input type="password" autoComplete="off" value={cfg.api_key||''} placeholder={cfg.api_key_set?'已保存；留空保留':'填写该服务的 API Key'} onChange={e=>change(role,{api_key:e.target.value})}/></label>
          <label className="check"><input type="checkbox" checked={!!cfg.cloud_consent} onChange={e=>change(role,{cloud_consent:e.target.checked})}/>我同意向 {cfg.base_url||'所填服务地址'} 发送本服务处理的课堂文本或课表图片；测试会发送示例文本／内置图片，费用由服务商收取。</label>
        </>}
      </>}
    </>;
  }
  function model(role:string,cfg:any) {
    const field = role==='text'?'text_model':'vision_model';
    return cfg.provider && cfg.provider!=='none' ? <>
      <label>{role==='text'?'文字模型':'看图模型'} ID<input list={`ai-models-${role}`} value={cfg[field]||''} placeholder="选择或填写服务商的准确模型 ID" onChange={e=>change(role,{[field]:e.target.value})}/></label>
      <datalist id={`ai-models-${role}`}>{(models[role]||[]).map(id=><option key={id} value={id}/>)}</datalist>
      <div className="actions"><button type="button" onClick={()=>void run(async()=>{await save();const r:any=await fetchJson(`/api/setup/ai/models?purpose=${role}`,{signal:AbortSignal.timeout(40000)});setModels({...models,[role]:r.models});setNotice(r.models.length?'模型列表已获取，点击模型输入框选择；列表不代表看图能力。':'服务未返回模型，可手动填写。');})}>保存并获取模型列表</button>
      <button type="button" disabled={!cfg[field]} onClick={()=>void run(async()=>{setVerified(v=>({...v,[role]:''}));const saved=await save();await fetchJson(`/api/setup/ai/test?purpose=${role}`,{method:'POST',signal:AbortSignal.timeout(610000)});setVerified(v=>({...v,[role]:JSON.stringify(role==='text'?saved:saved.vision_reuse===false?saved.vision_service:saved)}));setNotice(role==='text'?'文字模型连接与 JSON 输出验证通过。':'看图模型已通过内置图片识别测试。');})}>保存并测试{role==='text'?'文字模型':'看图模型'}</button></div>
      <p>{verified[role]===fingerprint(role)?'已验证':'尚未验证当前模型'}</p>
    </> : null;
  }
  return <fieldset disabled={busy} className="ai-settings">
    <p>文字模型用于总结和内容提取，看图模型用于课表截图识别。模型 ID 是服务商提供的名称；同一账号的多个模型通常可以共用一个 Key。</p>
    <h3>文字处理</h3>{service('text',value)}{model('text',value)}
    <h3>图片识别 · 可稍后配置</h3>
    <label>看图服务来源<select aria-label="看图服务来源" value={reuse?'reuse':'separate'} onChange={e=>{setVerified({});onChange({...value,vision_reuse:e.target.value==='reuse'});}}><option value="reuse">共用文字服务和 API Key</option><option value="separate">单独配置服务和 API Key</option></select></label>
    {!reuse && service('vision',vision)}{model('vision',vision)}
    <p>同一个支持图片的模型可以同时用于两项任务。暂不开启 AI 仍可使用课表、资料同步和平台原文。</p>
    <button type="button" onClick={()=>void run(async()=>{await save();setNotice('AI 配置已保存。');})}>保存 AI 配置</button>
    <p role="status">{notice}</p><p>密钥加密保存在本机，不写入设置草稿。离开前请保存。</p>
  </fieldset>;
}
