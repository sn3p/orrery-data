// Candidate-only adapter. The hosted schema-2 adapter remains unchanged in scope.
import { CometSource, interpolateComet, interpolateCometState } from './cometSourceCore.js';
import { fetchLatest, fetchVerified, decodeJSON, check, aborted } from './transport.js';
import { keys } from './contract.js';
import { T, END, reference, requireHistory, validateHistoryIndex, validateSegment } from './cometHistoryContract.js';

export default class CometHistorySource extends CometSource {
  static async openLatest(url, { signal } = {}) {
    const {location,latest} = await fetchLatest(url,signal,'comet');
    keys(latest,['comet_contract_version','index'],[],'comet history');
    requireHistory(latest.comet_contract_version === 3,'contract version');
    reference(latest.index,'index-',256*1024);
    const indexURL = new URL(latest.index.url,location).href;
    const bytes = await fetchVerified(latest.index,indexURL,signal,'comet');
    check(signal,'comet');
    const index = validateHistoryIndex(decodeJSON(bytes,'comet'));
    check(signal,'comet');
    return new CometHistorySource(indexURL,latest.index.sha256,index);
  }
  constructor(url,pin,index) {
    super(url,pin,index);
    this.entries = index.objects.flatMap(e=>e.segments.map(s=>({id:`${e.id}:${s.key}`,objectId:e.id,segment:s,data:s.data})));
    this.active = 0;
    this.queue = [];
  }
  object(id) {
    check(this.lifetime.signal,'comet');
    const entry = this.info.objects.find(e=>e.id===id);
    requireHistory(entry,'selection');
    return entry;
  }
  segmentAt(id,jd) {
    requireHistory(Number.isFinite(jd),'date');
    return this.object(id).segments.find(s=>jd>=s.coverage.start &&
      (jd<s.coverage.end || (jd===s.coverage.end && s.coverage.end_inclusive))) ?? null;
  }
  // No default date: callers must explicitly distinguish marker and path reads.
  async load(id,{jd,signal}={}) {
    check(signal,'comet');
    const segment = this.segmentAt(id,jd);
    return segment ? this.loadSegment(id,segment.key,{signal}) : null;
  }
  loadSegment(id,key,{signal}={}) {
    this.object(id);
    return super.load(`${id}:${key}`,{signal});
  }
  async positionAt(id,jd,{signal}={}) {
    const value = await this.load(id,{jd,signal});
    check(signal,'comet'); check(this.lifetime.signal,'comet');
    return value ? interpolateComet(value.series,jd) : null;
  }
  // Returns separate strips; support-only samples beyond the owned interval are
  // never exposed as path vertices. No connector or cross-file interpolation.
  async pathSegments(id,start,end,{signal}={}) {
    requireHistory(Number.isFinite(start)&&Number.isFinite(end)&&start<=end,'path range');
    check(signal,'comet');
    const wanted = this.object(id).segments.filter(s=>start<=s.coverage.end && end>=s.coverage.start
      && !(start===s.coverage.end && !s.coverage.end_inclusive));
    const strips = await Promise.all(wanted.map(async s=>{
      const value = await this.loadSegment(id,s.key,{signal}), series = value.series;
      const first = Math.max(start,s.coverage.start), last = Math.min(end,s.coverage.end);
      const times = [first];
      for (let i=Math.floor((first-series.start)/series.step)+1; series.start+i*series.step<last; i++) times.push(series.start+i*series.step);
      if(last>first) times.push(last);
      return {key:s.key,points:times.map(t=>[t,...interpolateComet(series,t)])};
    }));
    check(signal,'comet'); check(this.lifetime.signal,'comet');
    return strips;
  }
  validatePayload(value,entry) {
    validateSegment(value,entry);
    const object = this.object(entry.objectId);
    if (object.transition) {
      const otherKey = entry.segment.key === 'modern' ? 'historical' : 'modern';
      const other = this.cache.get(`${entry.objectId}:${otherKey}`);
      if(other) {
        const historical = entry.segment.key === 'historical' ? value : other;
        const modern = entry.segment.key === 'modern' ? value : other;
        for(const c of object.transition.comparisons) {
          const h = interpolateCometState(historical.series,c.jd), m = interpolateCometState(modern.series,c.jd);
          const dp = Math.hypot(...h.position.map((v,i)=>v-m.position[i]));
          const dv = Math.hypot(...h.velocity.map((v,i)=>v-m.velocity[i]));
          const valid = dp <= object.transition.position_limit_au && dv <= object.transition.velocity_limit_au_per_day
            && Math.abs(dp-c.position_difference_au)<=1e-14 && Math.abs(dv-c.velocity_difference_au_per_day)<=1e-14;
          if (!valid) this.cache.delete(`${entry.objectId}:${otherKey}`);
          requireHistory(valid,'transition evidence');
        }
      }
    }
    return value;
  }
  cacheValue(id,value) {
    this.cache.set(id,value);
    const bytes = () => [...this.cache.keys()].reduce((sum,key)=>sum+this.entries.find(e=>e.id===key).data.bytes,0);
    while(this.cache.size>8 || bytes()>16*1024*1024) this.cache.delete(this.cache.keys().next().value);
  }
  async fetchEntry(entry,signal) {
    await new Promise((resolve,reject)=>{
      const item = {start:()=>{signal.removeEventListener('abort',cancel);this.active++;resolve();}};
      const cancel = ()=>{
        const i=this.queue.indexOf(item); if(i>=0)this.queue.splice(i,1);
        signal.removeEventListener('abort',cancel); reject(aborted('comet'));
      };
      if(signal.aborted) { cancel(); return; }
      signal.addEventListener('abort',cancel,{once:true});
      this.queue.push(item); this.drain();
    });
    try { check(signal,'comet'); return await super.fetchEntry(entry,signal); }
    finally { this.active--; this.drain(); }
  }
  drain() { while(this.active<2 && this.queue.length) this.queue.shift().start(); }
}
export { T, END };
