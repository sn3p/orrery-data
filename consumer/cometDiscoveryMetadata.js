// Explicit, unpublished metadata sidecar. It cannot mutate trajectory state.
import {keys, requireValue, freeze} from './contract.js';
import {fetchVerified, decodeJSON, check} from './transport.js';
const need=(ok,label)=>requireValue(ok,label,'comet discovery metadata');
const fields=(v,required,optional=[])=>keys(v,required,optional,'comet discovery metadata');
const text=v=>typeof v==='string'&&v.length>0;
const hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
function date(d) {
  if(d===null)return;
  fields(d,['year','month','day','precision','calendar','year_numbering']);
  need(Number.isSafeInteger(d.year)&&Math.abs(d.year)<=9999&&d.year_numbering==='astronomical'
    && ['gregorian','historical-unspecified'].includes(d.calendar),'date');
  need(['year','month','day'].includes(d.precision),'precision');
  if(d.precision==='year'){need(d.month===null&&d.day===null,'year precision');return;}
  need(Number.isInteger(d.month)&&d.month>=1&&d.month<=12,'month');
  if(d.precision==='month'){need(d.day===null,'month precision');return;}
  const leap=d.year%4===0&&(d.year%100!==0||d.year%400===0);
  need(Number.isInteger(d.day)&&d.day>=1&&d.day<=[31,leap?29:28,31,30,31,30,31,31,30,31,30,31][d.month-1],'day');
}
export function validateDiscoveryMetadata(value, directory, legacy, pin) {
  fields(value,['discovery_metadata_version','directory_sha256','sources','objects']);
  need(value.discovery_metadata_version===1&&hash(pin)&&value.directory_sha256===pin,'directory pin');
  need(value.sources&&typeof value.sources==='object'&&!Array.isArray(value.sources),'sources');
  for(const s of Object.values(value.sources)) {
    fields(s,['url','checked_on'],['description','note']);
    let u;try{u=new URL(s.url);}catch{need(false,'source URL');}
    need(u.protocol==='https:'&&!u.username&&!u.password&&/^\d{4}-\d\d-\d\d$/.test(s.checked_on),'source');
  }
  // Preserve legacy records and attribution deeply, including nulls and notes.
  const same=(a,b)=>a===b||a!==null&&b!==null&&typeof a==='object'&&typeof b==='object'
    &&Array.isArray(a)===Array.isArray(b)&&Object.keys(a).length===Object.keys(b).length
    &&Object.keys(a).every(k=>Object.hasOwn(b,k)&&same(a[k],b[k]));
  for(const [key,s] of Object.entries(legacy.sources))need(same(value.sources[key],s),'legacy sources');
  need(Array.isArray(value.objects)&&value.objects.length===directory.objects.length,'inventory');
  for(const [i,row] of value.objects.entries()) {
    fields(row,['id','status','discovery']);need(row.id===directory.objects[i].id,'identity/order');
    const old=legacy.objects.find(o=>o.id===row.id);
    if(old){need(row.status==='preserved'&&same(row.discovery,old.discovery),'legacy preservation');continue;}
    need(['sourced','source-empty','outside-source-scope'].includes(row.status),'status');
    const d=row.discovery;
    fields(d,['id','catalogue_discovery','catalogue_provenance','catalogue_date','events','event_inventory_complete']);
    need(d.id===row.id&&d.event_inventory_complete===false,'metadata identity');
    date(d.catalogue_date);
    need(d.catalogue_discovery!==null||d.catalogue_date===null,'date without assertion');
    need(d.catalogue_discovery===null||d.catalogue_provenance!==null,'assertion provenance');
    need(d.catalogue_discovery===null||typeof d.catalogue_discovery==='object'&&!Array.isArray(d.catalogue_discovery)
      &&Object.values(d.catalogue_discovery).every(v=>v===null||typeof v==='string'),'catalogue assertions');
    if(d.catalogue_provenance!==null){
      const p=d.catalogue_provenance;fields(p,['source','sha256','representation','retrieved_at']);
      need(Object.hasOwn(value.sources,p.source)&&hash(p.sha256)&&p.representation==='canonical-json'
        &&text(p.retrieved_at)&&Number.isFinite(Date.parse(p.retrieved_at)),'provenance');
    }
    need(Array.isArray(d.events),'events');
    need(row.status==='sourced'?!!((d.catalogue_discovery&&Object.values(d.catalogue_discovery).some(Boolean))||d.events.length):(!d.catalogue_discovery||!Object.values(d.catalogue_discovery).some(Boolean))&&d.events.length===0,'status content');
    need(row.status==='outside-source-scope'?d.catalogue_provenance===null:row.status!=='source-empty'||d.catalogue_provenance!==null,'assessment provenance');
    for(const e of d.events) {
      fields(e,['kind','date','people','sources','certainty','note']);
      need(['discovery','independent_discovery','rediscovery','recovery','earlier_sighting','photographic_observation','discovery_recognition'].includes(e.kind),'event kind');
      need(['documented','day-disputed','approximate','uncertain'].includes(e.certainty),'certainty');
      need(e.certainty!=='day-disputed'||e.date?.day===null,'disputed day precision');
      date(e.date);need(e.date!==null||e.certainty==='uncertain','unknown event date');
      need(Array.isArray(e.people)&&e.people.every(text)&&Array.isArray(e.sources)&&e.sources.length>0
        &&e.sources.every(k=>Object.hasOwn(value.sources,k))&&(e.note===null||text(e.note)),'event attribution');
    }
  }
  return freeze(value);
}
// Caller supplies an explicit trusted reference; no latest/automatic activation.
export async function loadDiscoveryMetadata(source, reference, url, {signal}={}) {
  need(source.info.comet_contract_version===4&&source.legacy,'schema-4 source');
  need(reference&&hash(reference.sha256)&&Number.isSafeInteger(reference.bytes)&&reference.bytes>0&&reference.bytes<=8*1024*1024,'reference');
  const signals=[source.lifetime.signal,...(signal?[signal]:[])];
  signals.forEach(s=>check(s));
  const controller=new AbortController(),cancel=()=>controller.abort();
  signals.forEach(s=>s.addEventListener('abort',cancel,{once:true}));
  try {
    const parsed=decodeJSON(await fetchVerified(reference,url,controller.signal,'comet discovery metadata'));
    check(controller.signal);
    return validateDiscoveryMetadata(parsed,source.info,source.legacy.info,source.sourceId);
  } finally {signals.forEach(s=>s.removeEventListener('abort',cancel));}
}
