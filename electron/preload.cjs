'use strict';
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('collector', {
  call: async (method, data) => {
    const response = await ipcRenderer.invoke('collector:invoke', method, data);
    if (!response.ok) throw new Error(response.error);
    return response.data;
  },
  onState: callback => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('collector:state', listener);
    return () => ipcRenderer.removeListener('collector:state', listener);
  },
});
