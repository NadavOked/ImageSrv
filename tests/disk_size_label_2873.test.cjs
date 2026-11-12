// #2873: מגירה/דיסק מציגים את הגודל האמיתי; סולם הגדלים הוא רק שם של כונן שבאמת קרוב אליו.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const src=fs.readFileSync(path.resolve(__dirname,'../server/static/console.js'),'utf8');
function grab(name){
  const i=src.indexOf('function '+name+'(');
  assert.ok(i>=0, name+' missing');
  let d=0, j=src.indexOf('{',i);
  for(let k=j;k<src.length;k++){ if(src[k]==='{')d++; else if(src[k]==='}'&&--d===0) return src.slice(i,k+1); }
  throw new Error('unbalanced '+name);
}
const ctx={};
vm.createContext(ctx);
vm.runInContext(grab('fitsDisk')+grab('marketedDiskSize')+';this.m=marketedDiskSize',ctx);
const m=ctx.m;

test('a 40 GiB disk is not called 120GB', ()=>{
  assert.equal(m(40*2**30), '42.9GB');
  assert.equal(m(3*2**30), '3.2GB');
  assert.notEqual(m(40*2**30), '120GB');
});
test('real drives keep their market names', ()=>{
  assert.equal(m(120034123776), '120GB');
  assert.equal(m(256060514304), '256GB');
  assert.equal(m(500107862016), '500GB');
  assert.equal(m(1000204886016), '1TB');
});
test('a size between rungs says the real size, never the next rung', ()=>{
  assert.equal(m(300e9), '300.0GB');
  assert.equal(m(3e12), '3.0TB');
});
test('unknown stays a dash', ()=>{
  assert.equal(m(null), '—');
  assert.equal(m(0), '—');
});
