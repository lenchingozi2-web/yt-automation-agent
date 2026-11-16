import { normalizeDraft, type Draft, type Scene } from "./studio.ts";

export type ParsedImport = { drafts: Draft[]; notes: string[] };
export type ImportOptions = { format?: Draft["format"]; formatChosen?: boolean; durationSeconds?: number };
type Block = { heading?: string; lines: string[]; visuals: string[] };
const count = (s: string) => s.trim().split(/\s+/).filter(Boolean).length;
const ignore = new Set("the and of to in on it is that this with for from then about these those which what they you your have has was were who how why when there here some also will would could should video scene narration voiceover visuals images screen use show make".split(" "));
function duration(s: string): number {
  let m = /^(\d{1,2}):(\d\d)$/.exec(s.trim());
  if (m) return +m[1]*60 + +m[2];
  m = /(\d+(?:\.\d+)?)\s*(minutes?|mins?|min\b)/i.exec(s);
  if (m) return Math.round(+m[1]*60);
  m = /(\d+(?:\.\d+)?)\s*(seconds?|secs?|sec\b)/i.exec(s);
  return m ? Math.round(+m[1]) : 0;
}
function chunk(text: string): string[] {
  const words=text.replace(/\s+/g," ").trim().split(" ").filter(Boolean), out:string[]=[];
  for(let i=0;i<words.length;){
    let j=Math.min(i+42,words.length);
    if(j<words.length)for(let k=j-1;k>=i+23;k--)if(/[.!?]["')\]]*$/.test(words[k])){j=k+1;break;}
    out.push(words.slice(i,j).join(" "));i=j;
  }
  return out;
}
function keywords(narration: string, visuals:string[]): string[] {
  const visual=visuals.join("; ").split(/[;,]/).map(x=>x.replace(/^(show|display|cut to|use|image of|b-roll|on screen)\s+/i,"").trim()).filter(Boolean);
  if(visual.length)return [...new Set(visual.map(v=>v.split(/\s+/).slice(0,7).join(" ")))].slice(0,5);
  const terms=(narration.match(/[\p{L}][\p{L}\p{N}-]{3,}/gu)||[]).filter(w=>!ignore.has(w.toLowerCase()));
  return [terms.slice(0,4).join(" ")||"educational technology"];
}
function readContent(content:string): {draft:Draft; seconds:number; notes:string[]; formatFound:boolean} {
  let title="",description="",thumbnail="",format:Draft["format"]="16:9",formatFound=false,seconds=0,timeline=0;
  let tags:string[]=[],mode:"speech"|"visual"|"skip"="speech",started=false,inFence=false,foundCode=false;
  let current:Block={lines:[],visuals:[]};
  const blocks:Block[]=[];
  const flush=()=>{if(current.lines.some(Boolean))blocks.push(current);current={lines:[],visuals:[]};};
  for(const raw of content.replace(/\r\n?/g,"\n").split("\n")){
    const line=raw.trim().replace(/^>\s?/,"");
    if(line.startsWith(String.fromCharCode(96,96,96))||line.startsWith("~~~")){inFence=!inFence;foundCode=true;continue;}
    if(inFence)continue;
    if(!line){if(mode==="speech")current.lines.push("");continue;}
    const clean=line.replace(/^#{1,6}\s*/,"").replace(/\*\*/g,"").trim();
    const meta=/^(title|description|thumbnail(?:\s+(?:title|headline|text))?|tags|keywords|format|aspect\s*ratio|orientation|duration|length)\s*:\s*(.*)$/i.exec(clean);
    if(meta&&!started){
      const key=meta[1].toLowerCase(),v=meta[2].trim();
      if(key==="title")title=v;
      else if(key==="description")description=v;
      else if(key.startsWith("thumbnail"))thumbnail=v;
      else if(key==="tags"||key==="keywords")tags=v.split(/[,;]/).map(x=>x.trim()).filter(Boolean);
      else if(key==="duration"||key==="length")seconds=duration(v);
      else{if(/9\s*:\s*16|portrait|vertical/i.test(v)){format="9:16";formatFound=true;}
        else if(/16\s*:\s*9|landscape|horizontal/i.test(v)){format="16:9";formatFound=true;}}
      continue;
    }
    const times=/(\d{1,2}:\d\d)\s*[-–—]\s*(\d{1,2}:\d\d)/.exec(clean);
    if(times)timeline=Math.max(timeline,duration(times[2]));
    const field=/^(narration|voice\s*over|voiceover|spoken|script|visuals?|b-roll|footage|on[- ]screen|camera|shots?|music|sfx|notes?|directions?)\s*:\s*(.*)$/i.exec(clean.replace(/^\[|\]$/g,""));
    if(field){
      const isSpeech=/^(narration|voice|spoken|script)/i.test(field[1]);
      mode=isSpeech?"speech":/^(visual|b-roll|footage|on-|camera|shot|direction)/i.test(field[1])?"visual":"skip";
      if(mode==="speech"&&field[2]){current.lines.push(field[2]);started=true;}
      if(mode==="visual"&&field[2])current.visuals.push(field[2]);
      continue;
    }
    const marker=/^(?:#{1,5}\s+)(.+)$/.exec(line);
    const numbered=/^(?:(?:scene|segment|chapter|part)\s+\d+\s*(?:[:.\-–—]\s*|\s+)|\d{1,2}:\d\d\s*[-–—]\s*\d{1,2}:\d\d\s*[-–—:]?\s*)(.+)$/i.exec(clean);
    if(marker||numbered){
      const label=(marker?.[1]||numbered?.[1]||"").trim();
      if(!started&&!title&&marker&&!/^(opening|hook|intro|scene|narration|part|chapter|segment|script)/i.test(label))title=label;
      else if(/^(narration|voiceover|script)$/i.test(label))mode="speech";
      else if(/^(visuals?|footage|b-roll)$/i.test(label))mode="visual";
      else {
        const isInlineSpeech = Boolean(numbered && !marker && label.split(/\s+/).length >= 9);
        flush();
        current.heading = isInlineSpeech ? "Scene" : label.replace(/\d{1,2}:\d\d\s*[-–—]\s*\d{1,2}:\d\d/g, "").trim().slice(0, 80);
        mode="speech";started=true;
        if (isInlineSpeech) current.lines.push(label);
      }
      continue;
    }
    if(/^\[(visual|b-roll|footage|camera|on.screen)/i.test(line)){current.visuals.push(line.replace(/^\[|\]$/g,""));continue;}
    if(mode==="speech"){current.lines.push(clean.replace(/^[-*]\s+/,""));started=true;}
    else if(mode==="visual")current.visuals.push(clean.replace(/^[-*]\s+/,""));
  }
  flush();
  const scenes:Scene[]=[];
  for(const b of blocks)for(const [i,part] of chunk(b.lines.join(" ")).entries())scenes.push({
    scene_number:scenes.length+1,narration:part,search_queries:keywords(part,b.visuals),
    ...(i===0&&b.heading?{chapter_title:b.heading}:{}),
    ...(b.visuals.length?{visual_notes:b.visuals.join("\n")}:{})
  });
  if(!scenes.length)throw Error("No narration detected. Paste your full script, optionally using Narration: and Visuals: labels.");
  const notes:string[]=[];
  if(foundCode)notes.push("Code blocks were excluded from spoken narration; check the scene directions.");
  if(!title){title="Manual production";notes.push("No title detected. Add one in the editor.");}
  return {draft:{title,description,thumbnail_title:thumbnail||title,tags,format,duration_seconds:180,scenes},
    seconds:seconds||timeline,notes,formatFound};
}
function splitDraft(draft:Draft,requested:number):Draft[] {
  const weights=draft.scenes.map(s=>Math.max(1,count(s.narration))),wordCount=weights.reduce((a,b)=>a+b,0);
  const seconds=Math.max(60,requested,Math.ceil(wordCount*60/225));
  const parts=Math.max(Math.ceil(seconds/720),Math.ceil(draft.scenes.length/60));
  if(parts>24)throw Error("Too many scenes for a single import. Divide this script into smaller blocks.");
  const groups:Scene[][]=Array.from({length:parts},()=>[]);
  let cursor=0,remaining=wordCount;
  for(let part=0;part<parts;part++){
    let weight=0;
    const target=remaining/(parts-part);
    while(cursor<draft.scenes.length-(parts-part-1)&&groups[part].length<60){
      if(groups[part].length&&weight>=target)break;
      if(groups[part].length&&weight+weights[cursor]>target*1.1)break;
      groups[part].push(draft.scenes[cursor]);weight+=weights[cursor];cursor++;
    }
    if(!groups[part].length){groups[part].push(draft.scenes[cursor]);weight+=weights[cursor++];}
    remaining-=weight;
  }
  if(cursor!==draft.scenes.length)throw Error("Script exceeds Studio scene limits. Try smaller content blocks.");
  return groups.map((scenes,i)=>{
    const n=scenes.reduce((sum,s)=>sum+count(s.narration),0);
    const suffix=parts>1?" (Part "+(i+1)+" of "+parts+")":"";
    return {...draft,title:draft.title.slice(0,100-suffix.length)+suffix,
      duration_seconds:Math.min(720,Math.max(60,Math.round(seconds*n/wordCount))),
      scenes:scenes.map((s,j)=>({...s,scene_number:j+1}))};
  });
}
export function parseFullScript(input:string,options:ImportOptions={}):ParsedImport {
  const raw=input.replace(/^\uFEFF/,"").trim();
  if(!raw)throw Error("Paste your complete video content.");
  if(raw.length>180000)throw Error("This paste is too large. Use a block smaller than 180,000 characters.");
  let json:string=raw;
  const marker=String.fromCharCode(96,96,96);
  if(raw.startsWith(marker)){
    const first=raw.indexOf("\n"),last=raw.lastIndexOf(marker);
    if(first>0&&last>first)json=raw.slice(first+1,last).trim();
  }
  let object:unknown;
  try{object=JSON.parse(json);}catch{/* written script, not JSON */}
  let draft:Draft,seconds=0,notes:string[]=[],found=false;
  if(object&&typeof object==="object"){
    const rec=object as Record<string,unknown>;
    if(Array.isArray(object))draft=normalizeDraft({scenes:object});
    else if(Array.isArray(rec.scenes)||rec.script)draft=normalizeDraft(object);
    else throw Error("JSON requires a scenes array. Paste a full Studio draft or a written script.");
    if(!draft.scenes.length)throw Error("This JSON contains no scenes.");
    const original=Array.isArray(object)?object:Array.isArray(rec.scenes)?rec.scenes:[];
    draft.scenes=draft.scenes.map((s,i)=>({...s,
      ...(typeof (original[i] as Record<string,unknown>)?.visual_notes==="string"?
        {visual_notes:(original[i] as Record<string,string>).visual_notes}:{})}));
    seconds=draft.duration_seconds;found=true;
  } else {
    const p=readContent(raw);
    draft=p.draft;seconds=p.seconds;notes=p.notes;found=p.formatFound;
  }
  if(options.format&&(!found||options.formatChosen))draft.format=options.format;
  const wordCount=draft.scenes.reduce((n,s)=>n+count(s.narration),0);
  const selected=options.durationSeconds||seconds||Math.ceil(wordCount*60/145);
  if(options.durationSeconds&&selected<Math.ceil(wordCount*60/225))
    notes.push("Duration extended to preserve every spoken word.");
  const drafts=splitDraft(draft,selected);
  if(drafts.length>1)notes.push("Script imported as "+drafts.length+" consecutive parts (12-minute render maximum). Review each part without repasting.");
  return {drafts,notes};
}
