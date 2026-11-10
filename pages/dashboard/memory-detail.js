/** The stored retrieval text may contain both narrative and indexed facts. */
export function summaryText(memory) {
  const metadata = memory?.metadata || {};
  for (const key of ['persona_summary', 'summary']) {
    if (typeof metadata[key] === 'string' && metadata[key].trim()) return metadata[key].trim();
  }
  const content = String(memory?.content || '').trim();
  const facts = (metadata.key_facts || []).filter(fact => typeof fact === 'string' && fact.trim());
  // Strip only an exact, known fact appendix. A legitimate pipe in prose is safe.
  for (const separator of ['；', ';', '\n']) {
    const suffix = ' | ' + facts.join(separator);
    if (facts.length && content.endsWith(suffix)) return content.slice(0, -suffix.length).trim();
  }
  return content;
}

export function uniqueFacts(memory) {
  const seen = new Set();
  return (memory?.metadata?.key_facts || []).filter(value => {
    if (typeof value !== 'string' || !value.trim()) return false;
    const key = value.replace(/\s+/g, '').replace(/[。；;]+$/g, '');
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).map(value => value.trim());
}

/** Build the selected memory's graph directly, including records outside the overview cap. */
export function buildMemoryGraph(memory, memories = []) {
  const nodes = new Map(), edges = new Map();
  const entity = (kind, label) => ({ id: `${kind}:${label}`, label, kind });
  const root = { id: `memory:${memory.id}`, label: summaryText(memory).slice(0, 28), kind: 'memory', memory_id: memory.id };
  const putEdge = (source, target, label) => edges.set(`${source}\0${target}\0${label}`, { source, target, label });
  const entries = item => {
    const m = item.metadata || {}, result = [];
    for (const [field, kind, relation, limit] of [['topics','topic','涉及话题',12], ['participants','person','涉及人物',12], ['key_facts','fact','记录事实',20]]) {
      for (const value of (Array.isArray(m[field]) ? m[field] : []).slice(0,limit)) {
        if (typeof value === 'string' && value.trim()) result.push({ ...entity(kind,value.trim()), relation });
      }
    }
    for (const r of (Array.isArray(m.relations) ? m.relations : []).slice(0,30)) {
      if (r.status && r.status !== 'active') continue;
      for (const field of ['source','target']) if (typeof r[field] === 'string' && r[field].trim()) result.push({ ...entity('entity',r[field].trim()), kind:'fact', relation:'关系来源' });
    }
    return result;
  };
  nodes.set(root.id,root);
  const anchors = new Set();
  for (const item of entries(memory)) { nodes.set(item.id,item); anchors.add(item.id); putEdge(root.id,item.id,item.relation); }
  for (const relation of (Array.isArray(memory.metadata?.relations) ? memory.metadata.relations : []).slice(0,30)) {
    if (relation.status && relation.status !== 'active') continue;
    const a=`entity:${String(relation.source || '').trim()}`, b=`entity:${String(relation.target || '').trim()}`;
    if (nodes.has(a) && nodes.has(b)) putEdge(a,b,String(relation.relation || '相关'));
  }
  const neighbors = memories.filter(item => item.id !== memory.id && item.status === 'active').map(item => ({item,shared:entries(item).filter(e=>anchors.has(e.id))})).filter(row=>row.shared.length).sort((a,b)=>b.shared.length-a.shared.length).slice(0,6);
  for (const {item,shared} of neighbors) {
    const id=`memory:${item.id}`;
    nodes.set(id,{id,label:summaryText(item).slice(0,28),kind:'memory',memory_id:item.id});
    for (const entry of shared) putEdge(id,entry.id,entry.relation);
  }
  return {nodes:[...nodes.values()],edges:[...edges.values()],related_memories:neighbors.length};
}
