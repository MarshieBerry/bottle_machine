-- Run this once in the Supabase SQL Editor.
-- The Pi reads/writes these tables using a private secret/service_role API key.

create table if not exists public.recycling_users (
  rfid text primary key,
  student_id text not null unique,
  total_points integer not null default 0 check (total_points >= 0),
  total_weight_kg numeric(10, 3) not null default 0 check (total_weight_kg >= 0),
  total_bottles integer not null default 0 check (total_bottles >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.recycling_sessions (
  event_id text primary key,
  kiosk_id text not null,
  rfid text not null references public.recycling_users(rfid) on delete cascade,
  weight_kg numeric(10, 3) not null check (weight_kg >= 0),
  bottle_count integer not null check (bottle_count > 0),
  points integer not null check (points >= 0),
  created_at timestamptz not null default now()
);

-- If this SQL was already run before ON DELETE CASCADE was added, update the
-- existing foreign key. Deleting a user will also delete that user's sessions.
alter table public.recycling_sessions
drop constraint if exists recycling_sessions_rfid_fkey;

alter table public.recycling_sessions
add constraint recycling_sessions_rfid_fkey
foreign key (rfid)
references public.recycling_users(rfid)
on delete cascade;

alter table public.recycling_users enable row level security;
alter table public.recycling_sessions enable row level security;

create or replace function public.finish_recycling_session(
  p_event_id text,
  p_kiosk_id text,
  p_rfid text,
  p_weight_kg numeric,
  p_bottle_count integer,
  p_points integer
)
returns setof public.recycling_users
language plpgsql
security definer
set search_path = public
as $$
begin
  if p_weight_kg < 0 or p_bottle_count <= 0 or p_points < 0 then
    raise exception 'Invalid session totals';
  end if;

  insert into public.recycling_sessions (
    event_id, kiosk_id, rfid, weight_kg, bottle_count, points
  ) values (
    p_event_id, p_kiosk_id, p_rfid, p_weight_kg, p_bottle_count, p_points
  );

  return query
  update public.recycling_users
  set total_points = total_points + p_points,
      total_weight_kg = total_weight_kg + p_weight_kg,
      total_bottles = total_bottles + p_bottle_count,
      updated_at = now()
  where rfid = p_rfid
  returning *;
end;
$$;

-- Only an elevated private API key should be able to call the totals function.
revoke execute on function public.finish_recycling_session(text, text, text, numeric, integer, integer)
from public, anon, authenticated;
grant execute on function public.finish_recycling_session(text, text, text, numeric, integer, integer)
to service_role;
