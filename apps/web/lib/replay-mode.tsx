export default function ReplayMode({value,onChange}:{value:string;onChange:(value:string)=>void}) {
  return <fieldset className="replay-options"><legend>处理方式</legend><div className="replay-cards">
    {[['text','文本模式','获取平台文字，不下载视频。'],['illustrated','图文模式','下载课堂回放，并从教师电脑共享屏幕中提取课件画面。']].map(([id,title,description])=><label key={id} className={`replay-card ${value===id?'selected':''}`}><span><input type="radio" name="replay-mode" value={id} checked={value===id} onChange={()=>onChange(id)}/><strong>{title}</strong></span><span>{description}</span></label>)}
  </div><p>课件画面来自回放截图，实际内容以回放中的共享屏幕为准。</p></fieldset>;
}
