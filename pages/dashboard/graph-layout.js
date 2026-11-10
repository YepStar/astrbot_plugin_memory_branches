/** Stable, bounded-speed force layout, independent of SVG and viewport size. */
export function prepareGraph(graph, limit = 700) {
  const sourceNodes = Array.isArray(graph.nodes) ? graph.nodes : [];
  const byId = new Map(sourceNodes.map(n => [String(n.id), n]));
  const links = new Map(), degree = new Map();
  for (const edge of graph.edges || []) {
    const a = String(edge.source), b = String(edge.target);
    if (a === b || !byId.has(a) || !byId.has(b)) continue;
    const key = JSON.stringify([a,b].sort());
    if (!links.has(key)) links.set(key,{source:a,target:b,labels:new Set()});
    if (edge.label) links.get(key).labels.add(String(edge.label));
  }
  for (const edge of links.values()) {
    degree.set(edge.source,(degree.get(edge.source)||0)+1);
    degree.set(edge.target,(degree.get(edge.target)||0)+1);
  }
  // Keep every possible memory first, then the entities connecting the most records.
  const nodes = [...byId.values()].sort((a,b) =>
    Number(b.kind === 'memory')-Number(a.kind === 'memory') ||
    (degree.get(String(b.id))||0)-(degree.get(String(a.id))||0)
  ).slice(0,limit).map(n=>({...n,id:String(n.id)}));
  const visible = new Set(nodes.map(n=>n.id));
  const edges = [...links.values()].filter(e=>visible.has(e.source)&&visible.has(e.target)).map(e=>({source:e.source,target:e.target,label:[...e.labels].join(' / ')}));
  return {nodes,edges,stats:{shown_nodes:nodes.length,total_nodes:byId.size,shown_edges:edges.length,total_edges:(graph.edges||[]).length,unique_edges:links.size,shown_memories:nodes.filter(n=>n.kind==='memory').length}};
}

export function createLayout(graph) {
  const count = graph.nodes.length;
  const nodes = graph.nodes.map((n,i) => {
    const radius = Math.sqrt((i+.5)/Math.max(1,count))*Math.max(70,Math.sqrt(count)*55);
    return {...n,...labelGeometry(n.label),x:Math.cos(i*2.399963)*radius,y:Math.sin(i*2.399963)*radius*.86,vx:0,vy:0,degree:0};
  });
  const lookup = new Map(nodes.map((n,i)=>[n.id,i]));
  const edges = graph.edges.map(e=>({...e,a:lookup.get(e.source),b:lookup.get(e.target)})).filter(e=>e.a!==undefined&&e.b!==undefined);
  for (const edge of edges) {nodes[edge.a].degree++;nodes[edge.b].degree++;}
  const fx = new Float64Array(count), fy = new Float64Array(count);
  let ticks = 0,packed=false;
  function step() {
    fx.fill(0);fy.fill(0);
    const alpha = Math.max(.04,Math.pow(.985,ticks));
    for (let i=0;i<count;i++) {fx[i]-=nodes[i].x*.001;fy[i]-=nodes[i].y*.001;}
    for (let i=0;i<count;i++) for(let j=i+1;j<count;j++) {
      let dx=nodes[j].x-nodes[i].x,dy=nodes[j].y-nodes[i].y;
      if (Math.abs(dx)+Math.abs(dy)<.001) {dx=.1+((i+j)%7)*.02;dy=.1;}
      const d2=dx*dx+dy*dy,dist=Math.sqrt(d2);
      const force=Math.min(3,450/(d2+40));
      const x=dx/dist*force,y=dy/dist*force;
      fx[i]-=x;fy[i]-=y;fx[j]+=x;fy[j]+=y;
    }
    for (const edge of edges) {
      const a=nodes[edge.a],b=nodes[edge.b],dx=b.x-a.x,dy=b.y-a.y,dist=Math.hypot(dx,dy)||.01;
      // Unit direction, degree-normalized strength: never a quadratic spring.
      const strength=.08/Math.sqrt(Math.max(1,a.degree,b.degree));
      const rest=(a.width+b.width)/2+110;
      const force=(dist-rest)*strength;
      const x=dx/dist*force,y=dy/dist*force;
      fx[edge.a]+=x;fy[edge.a]+=y;fx[edge.b]-=x;fy[edge.b]-=y;
    }
    let movement=0;
    for (let i=0;i<count;i++) {
      const n=nodes[i];
      if(n.fixed){n.vx=n.vy=0;continue;}
      n.vx=(n.vx+fx[i]*alpha)*.76;n.vy=(n.vy+fy[i]*alpha)*.76;
      const speed=Math.hypot(n.vx,n.vy),cap=3;
      if(speed>cap){n.vx*=cap/speed;n.vy*=cap/speed;}
      n.x+=n.vx;n.y+=n.vy;movement+=Math.hypot(n.vx,n.vy);
    }
    ticks++;
    const running=ticks<200 && (ticks<90 || movement/Math.max(1,count)>.018);
    if(!running&&!packed){placeLabels(nodes);packed=true;}
    return running;
  }
  return {nodes,edges,step,get ticks(){return ticks;}};
}

export function fittedView(nodes, width, height, padding=35) {
  if(!nodes.length)return {scale:1,x:width/2,y:height/2};
  const xs=nodes.map(n=>n.x),ys=nodes.map(n=>n.y);
  const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);
  const scale=Math.min(1.8,Math.max(.05,Math.min((width-padding*2)/Math.max(100,maxX-minX),(height-padding*2)/Math.max(100,maxY-minY))));
  return {scale,x:width/2-(minX+maxX)/2*scale,y:height/2-(minY+maxY)/2*scale};
}

/** Wrap labels into readable world-space boxes, independent of viewport size. */
export function labelGeometry(label) {
  const lines=[];let line='',width=0;
  for(const char of String(label||'')){
    const size=/[\u0000-\u00ff]/.test(char)?7.5:14;
    if(char==='\n'||width+size>224){lines.push(line);line='';width=0;if(lines.length===4)break;}
    if(char!=='\n'){line+=char;width+=size;}
  }
  if(line&&lines.length<4)lines.push(line);
  if(!lines.length)lines.push('未命名');
  const textLength=lines.join('').length;
  if(textLength<String(label||'').replace(/\n/g,'').length)lines[lines.length-1]+='…';
  const widths=lines.map(text=>[...text].reduce((sum,c)=>sum+(/[\u0000-\u00ff]/.test(c)?7.5:14),0));
  return {lines,width:Math.max(90,Math.max(...widths)+38),height:Math.max(36,lines.length*21+16)};
}

/** Pack collision-free label rectangles; the world is allowed to grow freely. */
export function placeLabels(nodes, gap=18) {
  const cell=300,index=new Map();
  const box=n=>({left:n.x-n.width/2-gap/2,right:n.x+n.width/2+gap/2,top:n.y-n.height/2-gap/2,bottom:n.y+n.height/2+gap/2});
  const keys=b=>{const result=[];for(let x=Math.floor(b.left/cell);x<=Math.floor(b.right/cell);x++)for(let y=Math.floor(b.top/cell);y<=Math.floor(b.bottom/cell);y++)result.push(x+','+y);return result;};
  const overlaps=(a,b)=>a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top;
  for(const n of nodes){
    const g=labelGeometry(n.label);Object.assign(n,g);
    const x=n.x,y=n.y;let placed=false;
    for(let attempt=0;attempt<12000;attempt++){
      const radius=Math.sqrt(attempt)*26,angle=attempt*2.399963;
      n.x=x+Math.cos(angle)*radius;n.y=y+Math.sin(angle)*radius;
      const bounds=box(n),near=new Set(keys(bounds).flatMap(key=>index.get(key)||[]));
      if([...near].some(other=>overlaps(bounds,other)))continue;
      for(const key of keys(bounds)){if(!index.has(key))index.set(key,[]);index.get(key).push(bounds);}
      placed=true;break;
    }
    if(!placed){n.x=Math.max(0,...[...index.values()].flat().map(bounds=>bounds.right))+n.width/2+gap*2;n.y=0;const bounds=box(n);for(const key of keys(bounds)){if(!index.has(key))index.set(key,[]);index.get(key).push(bounds);}}
  }
  return nodes;
}

export function readableView(nodes,width,height,focusId) {
  const focus=nodes.find(n=>n.id===focusId)||nodes[0];
  return {scale:1,x:width/2-(focus?.x||0),y:height/2-(focus?.y||0)};
}
