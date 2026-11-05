import {test} from 'node:test';
import assert from 'node:assert/strict';
import {S} from '../frontend/modules/state.js';
import {renderLatest} from '../frontend/modules/views.js';
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

test('submission IDs remain valid UUIDs in the HTTP preview fallback',()=>{
  const descriptor=Object.getOwnPropertyDescriptor(crypto,'randomUUID');
  Object.defineProperty(crypto,'randomUUID',{value:undefined,configurable:true});
  try{
    const ids=Array.from({length:50},uniqueId);
    assert.equal(new Set(ids).size,50);
    assert.ok(ids.every(id=>/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(id)));
  }finally{if(descriptor)Object.defineProperty(crypto,'randomUUID',descriptor);else delete crypto.randomUUID;}
});
