import { prepareGraph, createLayout, readableView } from './graph-layout.js?v=1.2.0';

/** Offline SVG renderer with fitted, stable layout and focused labels. */
export function renderGraph(svg, graph, select) {
  let W=Math.max(320,svg.clientWidth||1000),H=Math.max(240,svg.clientHeight||560);
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  const prepared=prepareGraph(graph),layout=createLayout(prepared),nodes=layout.nodes,focusId=String(graph.nodes?.[0]?.id||'');
  const ns='http://www.w3.org/2000/svg',colors={memory:'#5956d6',topic:'#a8a4ed',person:'#cc8799',fact:'#7a99cf'};
  svg.replaceChildren();
  const make=(tag,attrs={})=>{const el=document.createElementNS(ns,tag);for(const[k,v]of Object.entries(attrs))el.setAttribute(k,v);return el;};
  const group=make('g');svg.append(group);
  let drag=null,pan=null,scale=1,tx=0,ty=0,frame,stopped=false,interacted=false,active='';
  const transform=()=>group.setAttribute('transform',`translate(${tx} ${ty}) scale(${scale})`);
  const fit=()=>{({scale,x:tx,y:ty}=readableView(nodes,W,H,focusId));transform();};
  const neighbors=new Map(nodes.map(n=>[n.id,new Set()]));
  const edges=layout.edges.map(edge=>{
    const el=make('line',{stroke:'#a19ec7','stroke-opacity':'.15','stroke-width':.8});
    const title=make('title');title.textContent=edge.label;el.append(title);group.append(el);
    neighbors.get(edge.source).add(edge.target);neighbors.get(edge.target).add(edge.source);
    return {...edge,el};
  });
  function highlight(id){
    active=id;
    for(const n of nodes){const focused=n.id===id||neighbors.get(id)?.has(n.id);n.el.setAttribute('opacity',!id||focused?'1':'.16');n.labelEl.setAttribute('visibility','visible');}
    for(const e of edges){const connected=e.source===id||e.target===id;e.el.setAttribute('stroke-opacity',!id?'.15':connected?'.65':'.025');e.el.setAttribute('stroke-width',connected?'1.6':'.8');}
  }
  for(const node of nodes){
    const el=node.el=make('g',{tabindex:0,role:'button','aria-label':node.label});
    el.style.cursor='pointer';
    const card=make('rect',{x:-node.width/2,y:-node.height/2,width:node.width,height:node.height,rx:6,fill:'#ffffff','fill-opacity':'.96',stroke:node.kind==='memory'?'#c5bff1':'#e2e3ed','stroke-width':1});el.append(card);
    const circle=make('circle',{cx:-node.width/2+11,r:node.kind==='memory'?6:3.5,fill:colors[node.kind]||'#9293a6',stroke:'#fff','stroke-width':1});
    const title=make('title');title.textContent=node.label;circle.append(title);el.append(circle);
    node.labelEl=make('text',{class:'graph-label',visibility:'visible'});
    for(const [i,line] of node.lines.entries()){const span=make('tspan',{x:-node.width/2+24,y:-node.height/2+22+i*21});span.textContent=line;node.labelEl.append(span);}
    el.append(node.labelEl);group.append(el);
    el.addEventListener('mouseenter',()=>highlight(node.id));el.addEventListener('mouseleave',()=>{if(!drag)highlight('');});
    el.addEventListener('focus',()=>highlight(node.id));el.addEventListener('blur',()=>highlight(''));
    el.addEventListener('click',()=>select(node));el.addEventListener('keydown',e=>{if(e.key==='Enter')select(node);});
    el.addEventListener('pointerdown',e=>{e.stopPropagation();interacted=true;drag=node;node.fixed=true;el.setPointerCapture(e.pointerId);highlight(node.id);});
  }
  function paint(){
    for(const n of nodes)n.el.setAttribute('transform',`translate(${n.x} ${n.y})`);
    for(const e of edges){const a=nodes[e.a],b=nodes[e.b];e.el.setAttribute('x1',a.x);e.el.setAttribute('y1',a.y);e.el.setAttribute('x2',b.x);e.el.setAttribute('y2',b.y);}
  }
  function step(){
    if(stopped)return;
    let running=true;
    for(let i=0;i<3&&running;i++)running=layout.step();
    paint();if(!interacted)fit();
    if(running)frame=requestAnimationFrame(step);
  }
  const point=e=>{const p=svg.createSVGPoint();p.x=e.clientX;p.y=e.clientY;return p.matrixTransform(svg.getScreenCTM().inverse());};
  const onMove=e=>{const p=point(e);if(drag){drag.x=(p.x-tx)/scale;drag.y=(p.y-ty)/scale;paint();}else if(pan){tx=p.x-pan.x;ty=p.y-pan.y;transform();}};
  const onDown=e=>{interacted=true;const p=point(e);pan={x:p.x-tx,y:p.y-ty};svg.setPointerCapture(e.pointerId);};
  const onUp=()=>{if(drag)drag.fixed=false;drag=null;pan=null;highlight('');};
  const onWheel=e=>{e.preventDefault();interacted=true;const p=point(e),next=Math.max(.05,Math.min(8,scale*(e.deltaY<0?1.12:.89)));tx=p.x-(p.x-tx)*next/scale;ty=p.y-(p.y-ty)*next/scale;scale=next;transform();};
  svg.addEventListener('pointermove',onMove);svg.addEventListener('pointerdown',onDown);svg.addEventListener('pointerup',onUp);svg.addEventListener('pointercancel',onUp);svg.addEventListener('wheel',onWheel,{passive:false});
  const resize=()=>{if(svg.clientWidth>0){W=Math.max(320,svg.clientWidth);H=Math.max(240,svg.clientHeight||560);svg.setAttribute('viewBox',`0 0 ${W} ${H}`);if(!interacted)fit();}};
  const observer=typeof ResizeObserver!=='undefined'?new ResizeObserver(resize):null;observer?.observe(svg);
  if(!nodes.length){const label=make('text',{x:0,y:0,'text-anchor':'middle',fill:'#6e7485'});label.textContent='总结对话后，话题、人物和事实会在这里连起来。';group.append(label);}
  if(nodes.length){let running=true;while(running)running=layout.step();}
  paint();fit();
  return {stats:prepared.stats,reset(){interacted=false;fit();},destroy(){stopped=true;cancelAnimationFrame(frame);observer?.disconnect();svg.removeEventListener('pointermove',onMove);svg.removeEventListener('pointerdown',onDown);svg.removeEventListener('pointerup',onUp);svg.removeEventListener('pointercancel',onUp);svg.removeEventListener('wheel',onWheel);}};
}
