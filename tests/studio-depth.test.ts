import { test } from "node:test";
import assert from "node:assert/strict";
import { deepenResearch, depthProfile, validateResearchChapters, type DepthEvidence } from "../src/lib/studio-depth.ts";

const source = (id: number): DepthEvidence => ({id, title:`Evidence ${id}`, url:`https://pubmed.ncbi.nlm.nih.gov/${id}/`,
  publisher:`Journal ${id}`, text:`The independently indexed research record ${id} reports its population, methods, findings and limitations. `.repeat(5)});
const sources = Array.from({length:12},(_,i)=>source(i+1));
const outline = () => Array.from({length:8},(_,i)=>({title:`A distinct chapter ${i+1}`, question:`What does evidence question ${i+1} establish?`,
  key_points:[`Specific finding number ${i+1} is supported by the assigned research.`, `Limitation number ${i+1} requires a separate careful explanation.`],source_ids:[i+1]}));
const proposal = {queries:["study methods", "mechanism background", "independent comparison", "practical interpretation", "evidence limitations", "related experiments"]};

test("research depth grows with selected duration and short videos remain cheap",()=>{
  assert.equal(depthProfile(60),undefined);
  assert.equal(depthProfile(720),undefined);
  assert.ok(depthProfile(1200)!.minSources < depthProfile(1800)!.minSources);
  assert.equal(depthProfile(1800)!.chapters,8);
  assert.equal(depthProfile(1800)!.targetWords,4050);
});

test("30-minute selection expands two briefing sources and preserves the original source IDs",async()=>{
  let plans=0, searches=0;
  const result=await deepenResearch("The selected story",sources.slice(0,2),1800,{
    plan:async()=> ++plans===1 ? proposal : {sufficient:true,chapters:outline()},
    search:async()=>{searches++;return sources.slice(2,8);},
  });
  assert.equal(searches,3); // second wave is unnecessary once evidence is sufficient
  assert.equal(plans,2);
  assert.equal(result.evidence.length,8);
  assert.equal(result.evidence[0].url,sources[0].url);
  assert.equal(result.evidence[1].id,2);
  assert.equal(result.chapters.length,8);
});

test("short drafts neither crawl additional sources nor call the depth planner",async()=>{
  const result=await deepenResearch("Short story",sources.slice(0,2),60,{
    plan:async()=>{throw Error("Unexpected model request");}, search:async()=>{throw Error("Unexpected crawl");},
  });
  assert.equal(result.evidence.length,2);
  assert.equal(result.chapters.length,0);
});

test("thin research uses at most six query searches and cannot become a padded 30-minute draft",async()=>{
  let searches=0;
  await assert.rejects(deepenResearch("Thin story",sources.slice(0,2),1800,{
    plan:async()=>proposal, search:async()=>{searches++;return sources.slice(0,2);},
  }),/found 2 suitable sources/);
  assert.equal(searches,6);
});

test("retrieval failures are bounded and unverified reference pages cannot inflate the source count",async()=>{
  let searches=0;
  await assert.rejects(deepenResearch("Thin story",sources.slice(0,2),1800,{
    plan:async()=>proposal, search:async()=>{searches++;if(searches===1)throw Error("Upstream timeout");return [{...source(3),url:"https://en.wikipedia.org/wiki/Example"}];},
  }),/found 2 suitable sources/);
  assert.equal(searches,6);
});

test("an independent depth rejection stops drafting even when eight sources were found",async()=>{
  let plans=0;
  await assert.rejects(deepenResearch("Narrow story",sources.slice(0,2),1800,{
    plan:async()=>++plans===1?proposal:{sufficient:false,reason:"The sources repeat one preliminary finding"},search:async()=>sources,
  }),/without padding/);
  assert.equal(plans,2);
});

test("chapter plans reject repeated topics, repeated points and invented citations",()=>{
  for(const change of [
    (chapters:ReturnType<typeof outline>)=>{chapters[1].title=chapters[0].title;},
    (chapters:ReturnType<typeof outline>)=>{chapters[1].key_points[0]=chapters[0].key_points[0];},
    (chapters:ReturnType<typeof outline>)=>{chapters[1].source_ids=[99];},
    (chapters:ReturnType<typeof outline>)=>{chapters[1].key_points=[chapters[1].key_points[0],chapters[1].key_points[0]];},
  ]){const chapters=outline();change(chapters);assert.throws(()=>validateResearchChapters(chapters,sources,1800));}
  const sparse=outline().map(c=>({...c,source_ids:[1]}));
  assert.throws(()=>validateResearchChapters(sparse,sources,1800),/enough sources/);
});

test("depth packet sizes and outline repair requests stay bounded",async()=>{
  let plans=0;
  const result=await deepenResearch("Selected topic",sources.slice(0,2),1800,{
    plan:async()=>{plans++;return plans===1?proposal:plans===2?{sufficient:true,chapters:[]}:{sufficient:true,chapters:outline()};},
    search:async()=>Array.from({length:40},(_,i)=>({...source(i+1),text:"evidence ".repeat(2000)})),
  });
  assert.ok(result.evidence.length>=8 && result.evidence.length<=12);
  assert.equal(plans,3);
  assert.ok(result.evidence.every(s=>s.text.length<=4000));
});
