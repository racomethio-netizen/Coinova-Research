-- =============================================================================
-- 0015 NEXUS paper trading (docs/nexus/SIGNAL_ENGINE.md §9, decision D4).
--
-- Virtual balances only: no exchange connection, no transfers, no custody.
-- v1 is long-only spot. Users act exclusively through the SECURITY DEFINER
-- functions below (validated, rate-limited); fills are applied by the engine
-- (`nexus_engine`) against fresh public prices, with fees and slippage.
-- Fills are insert-only; archived accounts are kept, never deleted.
-- =============================================================================

create table public.paper_accounts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  name text not null default 'Paper account' check (char_length(name) between 1 and 60),
  quote_asset text not null default 'USDT' check (quote_asset = 'USDT'),
  starting_balance numeric(30, 8) not null check (starting_balance between 100 and 10000000),
  cash_balance numeric(30, 8) not null check (cash_balance >= 0),
  status text not null default 'active' check (status in ('active', 'archived')),
  created_at timestamptz not null default now(),
  archived_at timestamptz,
  check ((status = 'archived') = (archived_at is not null))
);
create unique index paper_accounts_one_active on public.paper_accounts (user_id) where status = 'active';

create table public.paper_positions (
  account_id uuid not null references public.paper_accounts (id) on delete cascade,
  pair_id bigint not null references public.market_pairs (id),
  user_id uuid not null references auth.users (id) on delete cascade,
  quantity numeric(30, 12) not null default 0 check (quantity >= 0),
  cost_basis numeric(30, 8) not null default 0 check (cost_basis >= 0),
  realized_pnl numeric(30, 8) not null default 0,
  stop_loss numeric(30, 8) check (stop_loss is null or stop_loss > 0),
  take_profit numeric(30, 8) check (take_profit is null or take_profit > 0),
  updated_at timestamptz not null default now(),
  primary key (account_id, pair_id),
  check (stop_loss is null or take_profit is null or stop_loss < take_profit)
);

create table public.paper_orders (
  id uuid primary key default gen_random_uuid(),
  account_id uuid not null references public.paper_accounts (id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  pair_id bigint not null references public.market_pairs (id),
  side text not null check (side in ('buy', 'sell')),
  order_type text not null check (order_type in ('market', 'limit')),
  quantity numeric(30, 12) not null check (quantity > 0),
  limit_price numeric(30, 8) check (limit_price is null or limit_price > 0),
  stop_loss numeric(30, 8) check (stop_loss is null or stop_loss > 0),
  take_profit numeric(30, 8) check (take_profit is null or take_profit > 0),
  signal_id uuid references public.signals (id),
  status text not null default 'pending' check (status in ('pending', 'filled', 'cancelled', 'rejected', 'expired')),
  reject_reason text check (reject_reason is null or char_length(reject_reason) <= 120),
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  closed_at timestamptz,
  check ((order_type = 'limit') = (limit_price is not null)),
  check (side = 'buy' or (stop_loss is null and take_profit is null)),
  check (stop_loss is null or take_profit is null or stop_loss < take_profit),
  check ((status = 'pending') = (closed_at is null))
);
create index paper_orders_pending_idx on public.paper_orders (created_at) where status = 'pending';
create index paper_orders_user_idx on public.paper_orders (user_id, created_at desc);

create table public.paper_fills (
  id bigint generated always as identity primary key,
  order_id uuid references public.paper_orders (id) on delete cascade,
  account_id uuid not null references public.paper_accounts (id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  pair_id bigint not null references public.market_pairs (id),
  side text not null check (side in ('buy', 'sell')),
  reason text not null check (reason in ('market', 'limit', 'stop_loss', 'take_profit')),
  price numeric(30, 8) not null check (price > 0),
  quantity numeric(30, 12) not null check (quantity > 0),
  fee numeric(30, 8) not null check (fee >= 0),
  realized_pnl numeric(30, 8),
  price_source_time timestamptz not null,
  created_at timestamptz not null default now()
);
create index paper_fills_account_idx on public.paper_fills (account_id, created_at desc);
-- Insert-only, except that deleting a user cascades (their virtual history goes with the account).
create or replace function public.paper_fills_guard()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'UPDATE' then
    raise exception 'paper_fills is insert-only';
  end if;
  -- Allowed only as part of deleting the owning account or user (cascades run after the parent row is gone).
  if not exists (select 1 from public.paper_accounts a where a.id = old.account_id)
     or not exists (select 1 from auth.users u where u.id = old.user_id) then
    return old;
  end if;
  raise exception 'paper_fills is insert-only';
end;
$$;
create trigger paper_fills_guard before update or delete on public.paper_fills
  for each row execute function public.paper_fills_guard();

create table public.paper_equity_snapshots (
  account_id uuid not null references public.paper_accounts (id) on delete cascade,
  taken_at timestamptz not null,
  equity numeric(30, 8) not null,
  cash numeric(30, 8) not null,
  primary key (account_id, taken_at)
);

-- ------------------------------------------------------------ user functions
create or replace function public.open_paper_account(p_starting_balance numeric default 10000, p_name text default 'Paper account')
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  uid uuid := auth.uid();
  aid uuid;
begin
  if uid is null then raise exception 'unauthorized' using errcode = '42501'; end if;
  if exists (select 1 from public.paper_accounts where user_id = uid and status = 'active') then
    raise exception 'an active paper account already exists' using errcode = '22023';
  end if;
  insert into public.paper_accounts (user_id, name, starting_balance, cash_balance)
  values (uid, coalesce(nullif(trim(p_name), ''), 'Paper account'), p_starting_balance, p_starting_balance)
  returning id into aid;
  return aid;
end;
$$;

-- Archive the active account (open orders are cancelled; positions and history are kept) and open a new one.
create or replace function public.reset_paper_account(p_starting_balance numeric default 10000)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  uid uuid := auth.uid();
  old_id uuid;
begin
  if uid is null then raise exception 'unauthorized' using errcode = '42501'; end if;
  select id into old_id from public.paper_accounts where user_id = uid and status = 'active' for update;
  if old_id is not null then
    update public.paper_orders set status = 'cancelled', closed_at = now() where account_id = old_id and status = 'pending';
    update public.paper_accounts set status = 'archived', archived_at = now() where id = old_id;
  end if;
  return public.open_paper_account(p_starting_balance);
end;
$$;

create or replace function public.place_paper_order(
  p_pair_id bigint, p_side text, p_type text, p_quantity numeric,
  p_limit_price numeric default null, p_stop_loss numeric default null, p_take_profit numeric default null,
  p_signal_id uuid default null
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  uid uuid := auth.uid();
  acct public.paper_accounts;
  ref numeric;
  held numeric;
  reserved numeric;
  oid uuid;
begin
  if uid is null then raise exception 'unauthorized' using errcode = '42501'; end if;
  select * into acct from public.paper_accounts where user_id = uid and status = 'active' for update;
  if acct.id is null then raise exception 'open a paper account first' using errcode = '22023'; end if;
  if not exists (select 1 from public.market_pairs where id = p_pair_id and enabled) then
    raise exception 'unknown pair' using errcode = '22023';
  end if;
  if p_side not in ('buy', 'sell') or p_type not in ('market', 'limit') then
    raise exception 'invalid side or order type' using errcode = '22023';
  end if;
  if p_quantity is null or p_quantity <= 0 or p_quantity > 1000000 then
    raise exception 'invalid quantity' using errcode = '22023';
  end if;
  if (p_type = 'limit') <> (p_limit_price is not null) then
    raise exception 'limit orders need a limit price; market orders must not have one' using errcode = '22023';
  end if;
  if p_side = 'sell' and (p_stop_loss is not null or p_take_profit is not null) then
    raise exception 'stop loss and take profit apply to buy orders (long-only)' using errcode = '22023';
  end if;
  -- Protection must bracket the reference price (limit price, else the latest price).
  ref := coalesce(p_limit_price, (select price from public.latest_prices where pair_id = p_pair_id));
  if ref is not null and ((p_stop_loss is not null and p_stop_loss >= ref) or (p_take_profit is not null and p_take_profit <= ref)) then
    raise exception 'stop loss must be below and take profit above the entry price' using errcode = '22023';
  end if;
  -- Long-only: a sell can never exceed the position minus quantities already on pending sells.
  if p_side = 'sell' then
    select coalesce(quantity, 0) into held from public.paper_positions where account_id = acct.id and pair_id = p_pair_id;
    select coalesce(sum(quantity), 0) into reserved from public.paper_orders
      where account_id = acct.id and pair_id = p_pair_id and side = 'sell' and status = 'pending';
    if coalesce(held, 0) - reserved < p_quantity then
      raise exception 'not enough quantity to sell (long-only paper trading)' using errcode = '22023';
    end if;
  end if;
  -- Flood control (exact, in SQL).
  if (select count(*) from public.paper_orders where user_id = uid and created_at > now() - interval '1 minute') >= 30
     or (select count(*) from public.paper_orders where user_id = uid and created_at > now() - interval '1 day') >= 1000 then
    raise exception 'limit_reached:paperOrders' using errcode = 'P0001';
  end if;
  if (select count(*) from public.paper_orders where account_id = acct.id and status = 'pending') >= 50 then
    raise exception 'limit_reached:paperOpenOrders' using errcode = 'P0001';
  end if;
  insert into public.paper_orders (account_id, user_id, pair_id, side, order_type, quantity, limit_price, stop_loss, take_profit, signal_id, expires_at)
  values (acct.id, uid, p_pair_id, p_side, p_type, p_quantity, p_limit_price, p_stop_loss, p_take_profit, p_signal_id,
          now() + case when p_type = 'market' then interval '5 minutes' else interval '30 days' end)
  returning id into oid;
  return oid;
end;
$$;

create or replace function public.cancel_paper_order(p_order_id uuid)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
begin
  if auth.uid() is null then raise exception 'unauthorized' using errcode = '42501'; end if;
  update public.paper_orders set status = 'cancelled', closed_at = now()
   where id = p_order_id and user_id = auth.uid() and status = 'pending';
  return found;
end;
$$;

create or replace function public.set_paper_protection(p_pair_id bigint, p_stop_loss numeric, p_take_profit numeric)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  ref numeric;
begin
  if auth.uid() is null then raise exception 'unauthorized' using errcode = '42501'; end if;
  ref := (select price from public.latest_prices where pair_id = p_pair_id);
  if ref is not null and ((p_stop_loss is not null and p_stop_loss >= ref) or (p_take_profit is not null and p_take_profit <= ref)) then
    raise exception 'stop loss must be below and take profit above the current price' using errcode = '22023';
  end if;
  update public.paper_positions p set stop_loss = p_stop_loss, take_profit = p_take_profit, updated_at = now()
   from public.paper_accounts a
   where a.id = p.account_id and a.user_id = auth.uid() and a.status = 'active' and p.pair_id = p_pair_id and p.quantity > 0;
  return found;
end;
$$;

do $$
declare f text;
begin
  foreach f in array array[
    'open_paper_account(numeric, text)', 'reset_paper_account(numeric)',
    'place_paper_order(bigint, text, text, numeric, numeric, numeric, numeric, uuid)',
    'cancel_paper_order(uuid)', 'set_paper_protection(bigint, numeric, numeric)'
  ] loop
    execute format('revoke execute on function public.%s from public, anon', f);
    execute format('grant execute on function public.%s to authenticated', f);
  end loop;
end;
$$;
revoke execute on function public.paper_fills_guard() from public, anon, authenticated;

-- ------------------------------------------------------------ RLS
alter table public.paper_accounts enable row level security;
alter table public.paper_positions enable row level security;
alter table public.paper_orders enable row level security;
alter table public.paper_fills enable row level security;
alter table public.paper_equity_snapshots enable row level security;

revoke all on public.paper_accounts, public.paper_positions, public.paper_orders, public.paper_fills, public.paper_equity_snapshots from anon;
revoke insert, update, delete, truncate on public.paper_accounts, public.paper_positions, public.paper_orders,
  public.paper_fills, public.paper_equity_snapshots from authenticated;

create policy "paper_accounts: own" on public.paper_accounts for select to authenticated using (user_id = (select auth.uid()));
create policy "paper_positions: own" on public.paper_positions for select to authenticated using (user_id = (select auth.uid()));
create policy "paper_orders: own" on public.paper_orders for select to authenticated using (user_id = (select auth.uid()));
create policy "paper_fills: own" on public.paper_fills for select to authenticated using (user_id = (select auth.uid()));
create policy "paper_snapshots: own" on public.paper_equity_snapshots for select to authenticated
  using (exists (select 1 from public.paper_accounts a where a.id = account_id and a.user_id = (select auth.uid())));

-- Engine: applies fills and protections, never creates accounts or orders.
grant select, update on public.paper_accounts, public.paper_orders to nexus_engine;
grant select, insert, update on public.paper_positions to nexus_engine;
grant select, insert on public.paper_fills, public.paper_equity_snapshots to nexus_engine;
grant usage on sequence public.paper_fills_id_seq to nexus_engine;
create policy "paper_accounts: engine" on public.paper_accounts for all to nexus_engine using (true) with check (true);
create policy "paper_positions: engine" on public.paper_positions for all to nexus_engine using (true) with check (true);
create policy "paper_orders: engine" on public.paper_orders for all to nexus_engine using (true) with check (true);
create policy "paper_fills: engine" on public.paper_fills for all to nexus_engine using (true) with check (true);
create policy "paper_snapshots: engine" on public.paper_equity_snapshots for all to nexus_engine using (true) with check (true);

-- Account deletion export: include paper trading in export_my_data().
create or replace function public.export_my_paper_data()
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
  select jsonb_build_object(
    'paper_accounts', (select coalesce(jsonb_agg(to_jsonb(a) order by a.created_at), '[]') from public.paper_accounts a where a.user_id = auth.uid()),
    'paper_orders', (select coalesce(jsonb_agg(to_jsonb(o) order by o.created_at), '[]') from public.paper_orders o where o.user_id = auth.uid()),
    'paper_fills', (select coalesce(jsonb_agg(to_jsonb(f) order by f.created_at), '[]') from public.paper_fills f where f.user_id = auth.uid()),
    'paper_positions', (select coalesce(jsonb_agg(to_jsonb(p)), '[]') from public.paper_positions p where p.user_id = auth.uid())
  );
$$;
revoke execute on function public.export_my_paper_data() from public, anon;
grant execute on function public.export_my_paper_data() to authenticated;
