export function importanceBand(value) {
  const n=Number(value);
  return n>=.8?'high':n>=.5?'medium':'low';
}

export function sortMemories(rows, byImportance=false) {
  return [...rows].sort((a,b)=>(byImportance?Number(b.importance)-Number(a.importance):0)||Number(b.created)-Number(a.created)||String(a.id).localeCompare(String(b.id)));
}
