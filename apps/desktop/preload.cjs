const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('sufe',{chooseDirectory:()=>ipcRenderer.invoke('choose-directory')});
