'use strict';
// Read only project media, never account tokens or the site's complete user store.
const snapshotScript = String.raw`(() => {
  const id = location.pathname.match(/^\/gallery\/(\d+)(?:\/|$)/)?.[1];
  if (!id) return null;
  const element = document.getElementById('beconfig-store_state');
  if (!element) return null;
  let state; try { state = JSON.parse(element.textContent); } catch { return null; }
  const project = state?.project?.project;
  if (!project || String(project.id) !== id || !Array.isArray(project.modules)) return null;
  const fields = ['id','__typename','imageSizes','components','width','height','embed','videoData','originalEmbed','fluidEmbed'];
  const value = {projectId:id, project:{id:project.id,name:project.name,matureAccess:project.matureAccess,
    publishedOn:project.publishedOn,conceived_on:project.conceived_on,
    owners:(project.owners || []).map(owner=>({displayName:owner.displayName,display_name:owner.display_name})),
    modules:project.modules.slice(0,301).map(module=>Object.fromEntries(fields.filter(key=>module[key]!=null).map(key=>[key,module[key]])))}};
  const encoded = JSON.stringify(value);
  return encoded.length < 1400000 ? JSON.parse(encoded) : null;
})()`;
module.exports = { snapshotScript };
