import {createHash} from 'node:crypto';
import {createReadStream,createWriteStream,existsSync,statSync,statfsSync,readFileSync,writeFileSync,appendFileSync,renameSync,rmSync} from 'node:fs';
import {Readable} from 'node:stream';
import {pipeline} from 'node:stream/promises';

/** Only complete validated ranges are retained. Credentials and URLs never enter the journal. */
export async function downloadFile(url,temporary,{headers={},storageRoot,onProgress,blockSize=32*1024**2,reserveBytes=5*1024**3}={}){
 const head=await fetch(url,{method:'HEAD',headers,redirect:'error',signal:AbortSignal.timeout(60000)});
 if(!head.ok)throw Error(`回放下载失败：${head.status}`);
 const total=Number(head.headers.get('content-length')||0),validator=head.headers.get('etag')||head.headers.get('last-modified');
 const ranged=total>0&&head.headers.get('accept-ranges')==='bytes';
 const identity={source:createHash('sha256').update(url).digest('hex'),total,validator};
 const journal=temporary+'.resume.json';let bytes=0;
 if(ranged&&validator&&existsSync(temporary)&&existsSync(journal)){
  try{const prior=JSON.parse(readFileSync(journal,'utf8'));if(prior.source===identity.source&&prior.total===total&&prior.validator===validator)bytes=statSync(temporary).size;}catch{}
 }
 if(bytes>total)bytes=0;
 const disk=statfsSync(storageRoot);
 if(disk.bavail*disk.bsize<Math.max(0,total-bytes)+reserveBytes)throw Error('存储空间不足：请释放空间后重试');
 if(!bytes)writeFileSync(temporary,Buffer.alloc(0));
 writeFileSync(journal,JSON.stringify(identity));
 try{
  if(ranged){
   while(bytes<total){
    const end=Math.min(total-1,bytes+blockSize-1);let data;
    for(let attempt=0;attempt<3;attempt++){
     try{
      const response=await fetch(url,{headers:{...headers,Range:`bytes=${bytes}-${end}`,...(validator?{'If-Range':validator}:{})},redirect:'error',signal:AbortSignal.timeout(90000)});
      if(response.status!==206||response.headers.get('content-range')!==`bytes ${bytes}-${end}/${total}`){await response.body?.cancel();throw Error('媒体分段响应不一致');}
      data=Buffer.from(await response.arrayBuffer());if(data.length!==end-bytes+1)throw Error('媒体分段不完整');break;
     }catch(e){if(attempt===2)throw e;}
    }
    appendFileSync(temporary,data);bytes+=data.length;onProgress?.({downloadedBytes:bytes,totalBytes:total});
   }
  }else{
   const response=await fetch(url,{headers,redirect:'error',signal:AbortSignal.timeout(14400000)});
   if(!response.ok||!response.body)throw Error(`回放下载失败：${response.status}`);
   await pipeline(Readable.fromWeb(response.body),async function*(source){for await(const chunk of source){bytes+=chunk.length;onProgress?.({downloadedBytes:bytes,totalBytes:total});yield chunk;}},createWriteStream(temporary));
   if(total&&bytes!==total)throw Error('媒体下载长度不一致');
  }
 }catch(e){if(!ranged||!validator){rmSync(temporary,{force:true});rmSync(journal,{force:true});}throw e;}
 const hash=createHash('sha256');for await(const chunk of createReadStream(temporary))hash.update(chunk);
 return {digest:hash.digest('hex'),bytes,total,journal};
}
