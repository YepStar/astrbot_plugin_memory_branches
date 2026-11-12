export function importanceBand(value) {
  const n=Number(value);
  return n>=.8?'high':n>=.5?'medium':'low';
}

export function sortMemories(rows, byImportance=false) {
  return [...rows].sort((a,b)=>(byImportance?Number(b.importance)-Number(a.importance):0)||Number(b.created)-Number(a.created)||String(a.id).localeCompare(String(b.id)));
}

// A deduplicated summary still has its own rollback checkpoint, even when it
// enriches an older memory instead of creating another one.
export function timelineRows(memories, nodes, {status='all', query='', byImportance=false, textOf=m=>m.content||''}={}) {
  const checkpoints=new Map(nodes.map(n=>[String(n.id),n]));
  const represented=new Set(memories.filter(m=>m.node!=null).map(m=>String(m.node)));
  const needle=String(query).toLocaleLowerCase();
  const rows=memories.filter(m=>(status==='all'||m.status===status)&&(!needle||textOf(m).toLocaleLowerCase().includes(needle)))
    .map(m=>({type:'memory',id:m.id,created:m.created,importance:m.importance,memory:m,node:checkpoints.get(String(m.node))||null}));
  if(status==='all')for(const node of nodes){
    if(!represented.has(String(node.id))&&(!needle||String(node.title).toLocaleLowerCase().includes(needle)))
      rows.push({type:'checkpoint',id:'node:'+node.id,created:node.created,importance:null,node});
  }
  return rows.sort((a,b)=>(byImportance?(a.type!==b.type?(a.type==='memory'?-1:1):Number(b.importance)-Number(a.importance)):0)
    ||Number(b.created)-Number(a.created)||String(a.id).localeCompare(String(b.id)));
}
