const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('sufe',{ready:()=>ipcRenderer.invoke('runtime-ready')});
