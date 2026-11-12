alter table public.research_ideas
    add column topic_area text check (topic_area in ('health', 'education', 'intelligence'));

alter table public.research_story_history rename column source_pmid to lead_url;
alter table public.research_story_history
    alter column lead_url type text using ('https://pubmed.ncbi.nlm.nih.gov/' || lead_url || '/'),
    add column title text;

update public.research_story_history as h
set title = i.title
from public.research_ideas as i
where h.lead_url = i."references"[1];

create or replace function public.replace_research_briefing(p_briefing_date date, p_ideas jsonb)
returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
begin
    perform pg_advisory_xact_lock(8040173201);
    if p_briefing_date is null or p_briefing_date > (now() at time zone 'Africa/Lagos')::date then
        raise exception 'Invalid briefing date';
    end if;
    if exists (select 1 from public.research_ideas where briefing_date >= p_briefing_date) then
        return false;
    end if;
    if jsonb_typeof(p_ideas) is distinct from 'array' or jsonb_array_length(p_ideas) <> 3 then
        raise exception 'A briefing must contain three stories';
    end if;
    if (select count(distinct lower(btrim(idea->>'title'))) from jsonb_array_elements(p_ideas) as entries(idea)) <> 3 then
        raise exception 'A briefing must contain three different titles';
    end if;
    if (select count(distinct idea->>'topic_area') from jsonb_array_elements(p_ideas) as entries(idea)
        where idea->>'topic_area' in ('health', 'education', 'intelligence')) <> 3 then
        raise exception 'Include one story from each channel topic';
    end if;
    if exists (
        select 1 from jsonb_array_elements(p_ideas) as entries(idea)
        where (select count(distinct url) from jsonb_array_elements_text(idea->'references') as refs(url)) < 2
        or exists (select 1 from jsonb_array_elements_text(idea->'references') as refs(url)
                   where url is null or url !~ '^https://(pubmed\.ncbi\.nlm\.nih\.gov/[0-9]+/|eric\.ed\.gov/\?id=E[JD][0-9]+)$')
    ) then
        raise exception 'Each story must cite distinct approved source URLs';
    end if;

    delete from public.research_story_history where briefing_date < p_briefing_date - 90;
    insert into public.research_story_history (lead_url, briefing_date, title)
    select idea->'references'->>0, p_briefing_date, btrim(idea->>'title')
    from jsonb_array_elements(p_ideas) as entries(idea);

    insert into public.research_ideas (briefing_date, position, title, hook_summary, "references", topic_area)
    select p_briefing_date, position, btrim(idea->>'title'), btrim(idea->>'hook_summary'),
           array(select jsonb_array_elements_text(idea->'references')), idea->>'topic_area'
    from jsonb_array_elements(p_ideas) with ordinality as entries(idea, position);

    delete from public.research_ideas where briefing_date < p_briefing_date;
    return true;
end;
$$;

revoke execute on function public.replace_research_briefing(date, jsonb) from public, anon, authenticated;
grant execute on function public.replace_research_briefing(date, jsonb) to service_role;
