create table public.research_ideas (
    id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),
    briefing_date date not null,
    position smallint not null check (position between 1 and 3),
    title text not null check (char_length(btrim(title)) between 10 and 160),
    hook_summary text not null check (char_length(btrim(hook_summary)) between 80 and 700),
    "references" text[] not null check (cardinality("references") between 2 and 3),
    status text not null default 'pending' check (status in ('pending', 'queued')),
    unique (briefing_date, position)
);

-- Keep only source IDs for a short deduplication window. Story text is rotated out.
create table public.research_story_history (
    source_pmid bigint primary key,
    briefing_date date not null
);

alter table public.research_ideas enable row level security;
alter table public.research_story_history enable row level security;
revoke all on public.research_ideas, public.research_story_history from public, anon, authenticated;
grant select, insert, update, delete on public.research_ideas, public.research_story_history to service_role;

create function public.replace_research_briefing(p_briefing_date date, p_ideas jsonb)
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
    if exists (
        select 1 from jsonb_array_elements(p_ideas) as entries(idea)
        where (select count(distinct url) from jsonb_array_elements_text(idea->'references') as refs(url)) < 2
        or exists (select 1 from jsonb_array_elements_text(idea->'references') as refs(url)
                   where url is null or url !~ '^https://pubmed\.ncbi\.nlm\.nih\.gov/[0-9]+/$')
    ) then
        raise exception 'Each story must cite distinct PubMed article URLs';
    end if;

    delete from public.research_story_history where briefing_date < p_briefing_date - 21;
    insert into public.research_story_history (source_pmid, briefing_date)
    select substring(idea->'references'->>0 from '/([0-9]+)/$')::bigint, p_briefing_date
    from jsonb_array_elements(p_ideas) as entries(idea);

    insert into public.research_ideas (briefing_date, position, title, hook_summary, "references")
    select p_briefing_date, position, btrim(idea->>'title'), btrim(idea->>'hook_summary'),
           array(select jsonb_array_elements_text(idea->'references'))
    from jsonb_array_elements(p_ideas) with ordinality as entries(idea, position);

    delete from public.research_ideas where briefing_date < p_briefing_date;
    return true;
end;
$$;

revoke execute on function public.replace_research_briefing(date, jsonb) from public, anon, authenticated;
grant execute on function public.replace_research_briefing(date, jsonb) to service_role;
