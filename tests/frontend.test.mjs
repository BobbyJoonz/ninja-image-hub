import {test} from 'node:test';
import assert from 'node:assert/strict';
import {S} from '../frontend/modules/state.js';
import {filterJobs, chartHasData, renderLatest} from '../frontend/modules/views.js';
import {uniqueId} from '../frontend/modules/dom.js';

const elements=new Map(['latestStatus','latestPreview'].map(id=>[id,{textContent:'',innerHTML:''}]));
globalThis.document={getElementById:id=>elements.get(id)};

test('empty studio clears stale generated output without crashing',()=>{
  S.jobs=[];elements.get('latestPreview').innerHTML='stale image';
  renderLatest();
  assert.equal(elements.get('latestStatus').textContent,'No active generation');
  assert.ok(!elements.get('latestPreview').innerHTML.includes('stale image'));
});

test('unchanged image outputs do not replace the preview DOM on each poll',()=>{
  S.jobs=[{id:'job-123456789',status:'completed',n:1,image_count:1,images:[{position:0,url:'/api/images/job/0',preview_url:'/api/previews/job/0',image_width:10,image_height:10,image_format:'PNG'}]}];
  renderLatest();
  assert.ok(elements.get('latestPreview').innerHTML.includes('/api/previews/job/0'));
  elements.get('latestPreview').innerHTML='same DOM sentinel';
  renderLatest();
  assert.equal(elements.get('latestPreview').innerHTML,'same DOM sentinel');
});

test('job filters narrow by status and free-text query',()=>{
  const jobs=[
    {id:'a1111111',prompt:'green triangle',account_id:'acc1',status:'completed'},
    {id:'b2222222',prompt:'blue circle',account_id:'acc2',status:'failed'},
    {id:'c3333333',prompt:'night city',account_id:'acc1',status:'queued'},
  ];
  const names={acc1:'Main',acc2:'Secondary'};
  assert.equal(filterJobs(jobs,'all','',names).length,3);
  assert.deepEqual(filterJobs(jobs,'active','',names).map(j=>j.id),['c3333333']);
  assert.deepEqual(filterJobs(jobs,'problems','',names).map(j=>j.id),['b2222222']);
  assert.deepEqual(filterJobs(jobs,'all','circle',names).map(j=>j.id),['b2222222']);
  assert.deepEqual(filterJobs(jobs,'all','secondary',names).map(j=>j.id),['b2222222']);
  assert.deepEqual(filterJobs(jobs,'completed','main',names).map(j=>j.id),['a1111111']);
  assert.equal(filterJobs(jobs,'all','no-such-job',names).length,0);
});

test('chart reports data only when a bucket has images',()=>{
  assert.equal(chartHasData([]),false);
  assert.equal(chartHasData([{images:0},{images:0}]),false);
  assert.equal(chartHasData([{images:0},{images:2}]),true);
  assert.equal(chartHasData(undefined),false);
});

test('reconciled unknown jobs leave the problems filter',()=>{
  const jobs=[
    {id:'a',prompt:'uncertain one',account_id:'x',status:'unknown',resolution:null},
    {id:'b',prompt:'uncertain two',account_id:'x',status:'unknown',resolution:'delivered_manually'},
    {id:'c',prompt:'hard failure',account_id:'x',status:'failed'},
  ];
  assert.deepEqual(filterJobs(jobs,'problems','',{}).map(j=>j.id),['a','c']);
  assert.deepEqual(filterJobs(jobs,'all','',{}).map(j=>j.id),['a','b','c']);
});

test('submission IDs remain valid UUIDs in the HTTP preview fallback',()=>{
  const descriptor=Object.getOwnPropertyDescriptor(crypto,'randomUUID');
  Object.defineProperty(crypto,'randomUUID',{value:undefined,configurable:true});
  try{
    const ids=Array.from({length:50},uniqueId);
    assert.equal(new Set(ids).size,50);
    assert.ok(ids.every(id=>/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(id)));
  }finally{if(descriptor)Object.defineProperty(crypto,'randomUUID',descriptor);else delete crypto.randomUUID;}
});
