'use strict';
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('collector', {
  call: async (method, data) => {
    const response = await ipcRenderer.invoke('collector:invoke', method, data);
    if (!response.ok) throw Object.assign(new Error(response.error), { code: response.code });
    return response.data;
  },
  onState: callback => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('collector:state', listener);
    return () => ipcRenderer.removeListener('collector:state', listener);
  },
  onResizePointer: callback => {
    const listener = (_event, pointer) => callback(pointer);
    ipcRenderer.on('collector:resize-pointer', listener);
    return () => ipcRenderer.removeListener('collector:resize-pointer', listener);
  },
});
