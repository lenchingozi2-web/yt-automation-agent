import { test } from "node:test";
import assert from "node:assert/strict";
import { researchedDraft } from "../src/lib/studio-research.ts";

test("30-minute briefing draft researches first, writes eight source-backed chapters and stays one production", async()=>{
  const original=globalThis.fetch, oldKey=process.env.GEMINI_API_KEY;
  process.env.GEMINI_API_KEY="fixture-only";
  let searches=0, chapters=0, audits=0, failAudits=false;
  const record=(n:number)=>({pmid:String(n),title:`Verified learning evidence ${n}`,journalTitle:`Journal ${n}`,
    pubYear:"2026",abstractText:`This source ${n} reports learning methods, the observed finding, a distinct comparison and its limitations. `.repeat(15)});
  globalThis.fetch=async(input,init)=>{
    const url=String(input);
    if(url.includes("europepmc")){
      const exact=new URL(url).searchParams.get("query")?.includes("EXT_ID:");
      if(!exact){
        searches++;
        assert.ok((new URL(url).searchParams.get("query")?.match(/TITLE_ABS:/g)??[]).length<=3,"Over-specific deep search can miss relevant context");
      }
      return Response.json({resultList:{result:exact?[record(1),record(2)]:Array.from({length:5},(_,i)=>record(3+(searches-1)*5+i))}});
    }
    if(url.includes("api.ies.ed.gov"))return Response.json({response:{docs:[]}});
    assert.ok(url.includes("generativelanguage.googleapis.com"),"Unexpected external request");
    const body=JSON.parse(String(init?.body));
    const prompt:string=body.contents[0].parts[0].text;
    let response:unknown;
    if(prompt.includes("Plan deeper research for ONE"))response={queries:["retrieval practice classroom learning memory outcomes", "feedback comparison", "independent replication", "retention mechanism", "classroom application", "study limitations"]};
    else if(prompt.includes("Act as an independent long-video research editor"))response={sufficient:true,chapters:Array.from({length:8},(_,i)=>({
      title:`Research chapter ${i+1}`,question:`What does distinct question ${i+1} establish?`,
      key_points:Array.from({length:4},(_,n)=>({point:`Research point ${i+1}.${n+1} requires a distinct explanation grounded in its assigned source.`,source_id:n%2?(i+1)%8+1:i+1})),source_ids:[i+1,(i+1)%8+1]}))};
    else if(prompt.includes("Fact-check this long-video outline"))response={issues:[]};
    else if(prompt.includes("You are an independent fact checker")){audits++;response={issues:failAudits?["Scene 1 claims an unsupported benefit"]:[]};}
    else if(prompt.includes("Fix every audit finding"))response={scenes:[]};
    else if(prompt.includes("You are a YouTube editorial packaging specialist"))response={candidates:[]};
    else{
      const packetLine=prompt.split("\n").find(line=>line.startsWith("{")&&line.includes('"chapter_plan"'));
      assert.ok(packetLine,"Chapter writer did not receive its research packet");
      const packet=JSON.parse(packetLine!);
      assert.ok(packet.evidence.length<=5,"Chapter prompts must not resend the entire packet");
      const id=packet.chapter_plan.source_ids[0];
      chapters++;
      const requested=/Plan around (\d+) scenes and (\d+) total spoken words/.exec(prompt)!;
      const sceneCount=Number(requested[1]), wordsPerScene=Math.round(Number(requested[2])/sceneCount);
      const narration=(n:number)=>`Journal 1 explains this illustrative learning example ${n}. The learner compares two study approaches, checks the reasoning carefully, then considers a distinct limitation before drawing a cautious conclusion from the available evidence.`.split(" ").slice(0,wordsPerScene).join(" ")+".";
      response={title:"How feedback changes what students learn",description:"A careful explanation of learning evidence.",tags:["learning"],thumbnail_title:"The Feedback Gap",
        chapter_title:packet.chapter_plan.title,scenes:Array.from({length:sceneCount},(_,i)=>({narration:narration((chapters-1)*sceneCount+i+1),search_queries:["student learning"],source_ids:[1,id]}))};
    }
    return Response.json({candidates:[{content:{parts:[{text:JSON.stringify(response)}]}}]});
  };
  try{
    const draft=await researchedDraft("Explain this story",1800,"16:9",[],{title:"Feedback and learning",hook_summary:"A source-checked learning finding.",references:["https://pubmed.ncbi.nlm.nih.gov/1/","https://pubmed.ncbi.nlm.nih.gov/2/"]});
    assert.equal(draft.duration_seconds,1800);
    assert.equal(draft.format,"16:9");
    assert.equal(chapters,8);
    assert.ok(searches>=3&&searches<=6);
    assert.ok(audits>=8);
    assert.ok(draft.sources!.length>=8);
    assert.equal(draft.scenes.filter(s=>s.chapter_title).length,8);
    assert.ok(draft.scenes.length<=160);
    assert.ok(draft.scenes.reduce((sum,s)=>sum+s.narration.split(/\s+/).length,0)>=3645);
    assert.ok(draft.description.includes("https://pubmed.ncbi.nlm.nih.gov/1/"));
    failAudits=true;
    await assert.rejects(researchedDraft("Explain this story",1800,"16:9",[],{title:"Feedback and learning",hook_summary:"A source-checked learning finding.",references:["https://pubmed.ncbi.nlm.nih.gov/1/","https://pubmed.ncbi.nlm.nih.gov/2/"]}),/claims its sources did not support/);
  }finally{
    globalThis.fetch=original;
    if(oldKey===undefined)delete process.env.GEMINI_API_KEY;else process.env.GEMINI_API_KEY=oldKey;
  }
});
