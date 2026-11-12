import test from 'node:test';
import assert from 'node:assert/strict';
import {timelineRows} from '../pages/dashboard/memory-presentation.js';
const memories=[{id:'m1',node:1,content:'music',created:100,importance:.7,status:'active'},{id:'m2',node:3,content:'book',created:300,importance:.9,status:'archived'}];
const nodes=[{id:1,title:'music',created:100},{id:2,title:'merged music',created:200},{id:3,title:'book',created:300}];
test('a memory and its node share one entry while deduplicated checkpoints remain reachable',()=>{
 const rows=timelineRows(memories,nodes);
 assert.equal(rows.length,3);
 assert.deepEqual(rows.map(r=>r.node.id),[3,2,1]);
 assert.equal(rows[1].type,'checkpoint');
 assert.equal(rows[2].memory.id,'m1');
});
test('filtering hides memories without exposing their nodes as extra records',()=>{
 const rows=timelineRows(memories,nodes,{status:'active'});
 assert.deepEqual(rows.map(r=>r.id),['m1']);
 assert.deepEqual(timelineRows(memories,nodes,{query:'merged'}).map(r=>r.node.id),[2]);
});
test('importance sorting keeps checkpoints available after scored memories',()=>{
 assert.deepEqual(timelineRows(memories,nodes,{byImportance:true}).map(r=>r.id),['m2','m1','node:2']);
});
test('migrated memories share one rollback target and missing targets cannot be guessed',()=>{
 const rows=timelineRows([{...memories[0],node:'1'},{...memories[1],node:1},{id:'legacy',node:null,created:400}],nodes.slice(0,1));
 assert.equal(rows.length,3);
 assert.equal(rows.find(r=>r.id==='legacy').node,null);
 assert.deepEqual(rows.filter(r=>r.node).map(r=>r.node.id),[1,1]);
});
