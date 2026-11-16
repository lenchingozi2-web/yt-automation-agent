import { normalizeDraft, type Draft, type Scene } from "./studio.ts";
import { MAX_VIDEO_SCENES, MAX_VIDEO_SECONDS, MAX_VIDEO_MINUTES, MIN_VIDEO_SECONDS } from "./studio-limits.ts";

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
  let tags:string[]=[],mode:"speech"|"visual"|"skip"|"description"="speech",started=false,inFence=false,foundCode=false;
  let current:Block={lines:[],visuals:[]};
  const blocks:Block[]=[];
  const flush=()=>{if(current.lines.some(Boolean))blocks.push(current);current={lines:[],visuals:[]};};
  for(const raw of content.replace(/\r\n?/g,"\n").split("\n")){
    const line=raw.trim().replace(/^>\s?/,"");
    if(line.startsWith(String.fromCharCode(96,96,96))||line.startsWith("~~~")){inFence=!inFence;foundCode=true;continue;}
    if(inFence)continue;
    if(!line){if(mode==="speech")current.lines.push("");continue;}
    const clean=line.replace(/^#{1,6}\s*/,"").replace(/\*\*/g,"").trim();
    if (/^(?:-{3,}|_{3,}|end of script)$/i.test(clean)) continue;
    const meta=/^(?:video\s+)?(title|description|thumbnail(?:\s+(?:title|headline|text))?|tags|keywords|format|aspect\s*ratio|orientation|duration|length)\s*:\s*(.*)$/i.exec(clean);
    if(meta&&!started){
      const key=meta[1].toLowerCase(),v=meta[2].trim();
      if(key==="title")title=v;
      else if(key==="description"){description=v;mode="description";}
      else if(key.startsWith("thumbnail"))thumbnail=v;
      else if(key==="tags"||key==="keywords")tags=v.split(/[,;]/).map(x=>x.trim()).filter(Boolean);
      else if(key==="duration"||key==="length")seconds=duration(v);
      else{if(/9\s*:\s*16|portrait|vertical/i.test(v)){format="9:16";formatFound=true;}
        else if(/16\s*:\s*9|landscape|horizontal/i.test(v)){format="16:9";formatFound=true;}}
      continue;
    }
    const times=/(\d{1,2}:\d\d)\s*[-–—]\s*(\d{1,2}:\d\d)/.exec(clean);
    if(times)timeline=Math.max(timeline,duration(times[2]));
    const field=/^(narration|voice\s*over|voiceover|spoken|script|visuals?(?:\s+(?:directions?|cues?|notes?))?|b-roll|footage|on[- ]screen(?:\s+(?:text|visuals?))?|camera|shots?|what\s+to\s+show|stage\s+directions?|music|sfx|notes?|directions?)\s*:\s*(.*)$/i.exec(clean.replace(/^\[|\]$/g,""));
    if(field){
      const isSpeech=/^(narration|voice|spoken|script)/i.test(field[1]);
      mode=isSpeech?"speech":/^(visual|b-roll|footage|on-|camera|shot|direction|what to show|stage direction)/i.test(field[1])?"visual":"skip";
      if(mode==="speech"&&field[2]){current.lines.push(field[2]);started=true;}
      if(mode==="visual"&&field[2])current.visuals.push(field[2]);
      continue;
    }
    if(/^(?:narration|voiceover|voice over|spoken|script)$/i.test(clean)){mode="speech";started=true;continue;}
    if(/^(?:visuals?|b-roll|footage|visual directions?|on[- ]screen(?: visuals?)?|what to show)$/i.test(clean)){mode="visual";continue;}
    if(/^(?:scene|segment|chapter)\s+\d+\s*:?$/i.test(clean)){flush();current.heading=clean;mode="speech";started=true;continue;}
    const marker=/^(?:#{1,5}\s+)(.+)$/.exec(line);
    const numbered=/^(?:(?:scene|segment|chapter|part)\s+\d+\s*(?:[:.\-–—]\s*|\s+)|\d{1,2}:\d\d\s*[-–—]\s*\d{1,2}:\d\d\s*[-–—:]?\s*)(.+)$/i.exec(clean);
    if(marker||numbered){
      const rawLabel=(marker?.[1]||numbered?.[1]||"").trim();
      const label=rawLabel.replace(/^(?:scene|segment|chapter|part)\s+\d+\s*(?:[:.\-–—]\s*|\s+)/i,"").trim() || rawLabel;
      if(!started&&!title&&marker&&!/^(opening|hook|intro|scene|narration|part|chapter|segment|script)/i.test(label))title=label;
      else if(/^(narration|voiceover|script)$/i.test(label))mode="speech";
      else if(/^(visuals?|footage|b-roll)$/i.test(label))mode="visual";
      else {
        const isInlineSpeech = Boolean(numbered && !marker && (label.split(/\s+/).length >= 5 || /[.!?]["”]*$/.test(label)));
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
    else if(mode==="description")description+=(description?"\n":"")+clean;
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
/** An import is always ONE video job. Internal scene boundaries are never separate uploads. */
function singleDraft(draft: Draft, requestedSeconds: number): Draft {
  const spokenWords = draft.scenes.reduce((sum, scene) => sum + count(scene.narration), 0);
  if (!spokenWords) throw Error("No spoken narration was found.");
  if (draft.scenes.length > MAX_VIDEO_SCENES)
    throw Error("This script contains " + draft.scenes.length + " scenes. The limit is " + MAX_VIDEO_SCENES + " scenes in a single video. Shorten or combine scene headings; nothing has been removed.");
  if (requestedSeconds > MAX_VIDEO_SECONDS)
    throw Error("This script requests " + Math.ceil(requestedSeconds / 60) + " minutes. Studio's current single-video maximum is " + MAX_VIDEO_MINUTES + " minutes. No part of your script was queued or discarded.");
  const minimumSpeechSeconds = Math.ceil(spokenWords * 60 / 185);
  if (minimumSpeechSeconds > MAX_VIDEO_SECONDS)
    throw Error("This script has " + spokenWords + " words, too many to narrate naturally within " + MAX_VIDEO_MINUTES + " minutes. Please shorten it; the importer will not cut or split your content.");
  const seconds = Math.max(MIN_VIDEO_SECONDS, requestedSeconds, minimumSpeechSeconds);
  return { ...draft, duration_seconds: seconds,
    scenes: draft.scenes.map((scene, index) => ({ ...scene, scene_number: index + 1 })) };
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
  if(selected<Math.ceil(wordCount*60/185))
    notes.push("The requested duration is too short for comfortable delivery. Studio extended it to preserve every spoken word.");
  const single = singleDraft(draft, selected);
  const estimatedSeconds = Math.ceil(wordCount * 60 / 145);
  if (estimatedSeconds > single.duration_seconds * 1.2)
    notes.push("Your spoken text may run longer than your selected target at a comfortable narration pace. Review the word count and duration before rendering; no words were removed.");
  if (estimatedSeconds < single.duration_seconds * .75)
    notes.push("The narration may finish sooner than the target duration. Studio follows the actual spoken audio rather than padding it with silence.");
  notes.push("All "+single.scenes.length+" scenes will render as ONE continuous MP4 and ONE upload approval.");
  return {drafts:[single],notes};
}
