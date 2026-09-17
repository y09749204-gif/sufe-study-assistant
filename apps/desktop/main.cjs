const {app,BrowserWindow,dialog,shell,ipcMain}=require('electron');
const {spawn}=require('node:child_process');
const path=require('node:path');
let host,window;
if(!app.requestSingleInstanceLock()){app.quit();}else{
app.on('second-instance',()=>{window?.show();window?.focus()});
app.whenReady().then(()=>{
 const root=app.isPackaged?path.join(process.resourcesPath,'app'):path.resolve(__dirname,'../..');
 const runtimes=app.isPackaged?path.join(process.resourcesPath,'runtime'):path.join(root,'runtime');
 const python=path.join(runtimes,'python','python.exe');
 const data=path.join(app.getPath('appData'),'SufeStudyAssistant');
 host=spawn(python,['-B',path.join(root,'scripts','desktop-host.py')],{cwd:root,windowsHide:true,env:{...process.env,SUFE_DATA_DIR:data,SUFE_RUNTIME_DIR:runtimes,PYTHONPATH:path.join(root,'apps/api')},stdio:['pipe','pipe','pipe']});
 window=new BrowserWindow({width:1280,height:860,minWidth:580,minHeight:600,title:'上财学业助手',webPreferences:{preload:path.join(__dirname,'preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true}});
 window.loadURL('data:text/html;charset=utf-8,'+encodeURIComponent('<meta charset="utf-8"><body style="font:16px system-ui;padding:50px;background:#f5f6f2"><h1>上财学业助手</h1><p>正在启动独立数据库和本机服务，首次启动需要片刻…</p></body>'));
 let buffer='',origin='';
 host.stdout.on('data',chunk=>{buffer+=chunk;let n;while((n=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,n);buffer=buffer.slice(n+1);try{const msg=JSON.parse(line);if(msg.url){origin=new URL(msg.url).origin;window.loadURL(msg.url);}if(msg.error)dialog.showErrorBox('启动失败',msg.error);}catch{}}});
 host.on('error',()=>dialog.showErrorBox('启动失败','运行环境未准备好。开发者请先运行 prepare-runtime.py。'));
 host.on('exit',code=>{if(code)dialog.showErrorBox('服务已退出','请查看应用数据目录中的 logs/host.log。')});
 window.webContents.setWindowOpenHandler(({url})=>{if(origin&&new URL(url).origin===origin)return {action:'allow',overrideBrowserWindowOptions:{webPreferences:{contextIsolation:true,nodeIntegration:false,sandbox:true}}};if(/^https?:\/\//.test(url))void shell.openExternal(url);return {action:'deny'}});
 window.webContents.on('will-navigate',(event,url)=>{if(origin&&new URL(url).origin!==origin)event.preventDefault()});
 window.webContents.session.setPermissionRequestHandler((_wc,_permission,callback)=>callback(false));
 window.on('closed',()=>app.quit());
});
app.on('before-quit',()=>{if(host?.stdin.writable){host.stdin.end('stop\n');}});
}
