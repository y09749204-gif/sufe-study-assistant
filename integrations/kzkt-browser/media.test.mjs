import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdtempSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {downloadFile} from './media.mjs';

test('interrupted range download resumes validated bytes after retry',async()=>{
 const data=Buffer.from('abcdefghijklmnop');let fail=true;const starts=[];
 const server=createServer((req,res)=>{res.setHeader('Content-Length',data.length);res.setHeader('Accept-Ranges','bytes');res.setHeader('ETag','"v1"');if(req.method==='HEAD'){res.end();return;}
 const [a,b]=req.headers.range.match(/\d+/g).map(Number);starts.push(a);
 if(a>=4&&fail){res.writeHead(503,{'Content-Length':0});res.end();return;}
 res.writeHead(206,{'Content-Length':b-a+1,'Content-Range':`bytes ${a}-${b}/${data.length}`});res.end(data.subarray(a,b+1));});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const root=mkdtempSync(join(tmpdir(),'sufe-media-'));const file=join(root,'clip.part');
 try{const url=`http://127.0.0.1:${server.address().port}/clip`;const options={storageRoot:root,blockSize:4,reserveBytes:0};
 await assert.rejects(downloadFile(url,file,options));assert.equal(readFileSync(file).toString(),'abcd');fail=false;starts.length=0;
 const result=await downloadFile(url,file,options);assert.equal(starts[0],4);assert.equal(result.bytes,16);assert.deepEqual(readFileSync(file),data);
 }finally{server.closeAllConnections();await new Promise(r=>server.close(r));rmSync(root,{recursive:true,force:true});}
});

test('server failure never reports a completed download',async()=>{
 const server=createServer((_req,res)=>{res.writeHead(403);res.end();});await new Promise(r=>server.listen(0,'127.0.0.1',r));const root=mkdtempSync(join(tmpdir(),'sufe-media-'));
 try{await assert.rejects(downloadFile(`http://127.0.0.1:${server.address().port}/clip`,join(root,'clip.part'),{storageRoot:root,reserveBytes:0}),/403/);}
 finally{server.closeAllConnections();await new Promise(r=>server.close(r));rmSync(root,{recursive:true,force:true});}
});
