// #857: adding/deleting/importing a machine answers `network: {applied, error}`.
// When the known-macs/dnsmasq update failed, the console must say so -- the
// machine was saved but will not get GRUB until the network is fixed. Until
// now only the presence of the text was checked (test_known_macs.py); this
// runs the function itself, so a dhcpNotice() that swallows the failure fails.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=process.env.CONSOLE_STATIC_ROOT || path.resolve(__dirname,'../server/static');

function load() {
  const src=fs.readFileSync(path.join(root,'console.js'),'utf8');
  const start=src.indexOf('function dhcpNotice(');
  assert.ok(start>=0,'dhcpNotice is gone from console.js');
  const end=src.indexOf('\n}\n',start);
  assert.ok(end>start,'dhcpNotice has no closing brace at column 0');
  const toasts=[];
  const ctx=vm.createContext({toast:(text)=>toasts.push(String(text))});
  vm.runInContext(src.slice(start,end+2),ctx);
  return {dhcpNotice:ctx.dhcpNotice,toasts};
}

test('a failed DHCP update is shown, with the reason, and reported as shown',()=>{
  const {dhcpNotice,toasts}=load();
  const shown=dhcpNotice({mac:'aa:bb:cc:dd:ee:ff',network:{applied:false,error:'dnsmasq reload failed'}});
  assert.equal(shown,true);
  assert.equal(toasts.length,1);
  assert.match(toasts[0],/המכונה נשמרה, אבל ה-DHCP לא עודכן/);
  assert.match(toasts[0],/dnsmasq reload failed/);
});

test('a failure without a reason is still shown',()=>{
  const {dhcpNotice,toasts}=load();
  assert.equal(dhcpNotice({ok:true,network:{applied:false,error:null}}),true);
  assert.match(toasts[0],/סיבה לא ידועה/);
});

test('success, no hooks (network null) and no answer stay silent',()=>{
  const {dhcpNotice,toasts}=load();
  assert.equal(dhcpNotice({mac:'aa',network:{applied:true,error:null}}),false);
  assert.equal(dhcpNotice({mac:'aa',network:null}),false);
  assert.equal(dhcpNotice(null),false);
  assert.deepEqual(toasts,[]);
});
