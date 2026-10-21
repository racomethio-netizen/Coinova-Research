-- =============================================================================
-- DEVELOPMENT-ONLY SEED DATA. NEVER RUN AGAINST A PRODUCTION DATABASE.
--
-- * Lets the billing simulator grant entitlements (test-mode subscriptions).
-- * Inserts clearly-labeled sample research articles so pages have content.
-- Coins and market data come from running the refresh-markets job against the
-- configured provider (the mock provider in development).
-- =============================================================================

update public.app_config set value = 'false'::jsonb where key = 'require_livemode_subscriptions';

with a as (
  insert into public.research_articles (slug, title, summary, category, access_level, status, author_name, published_at, tags, data_sources)
  values
    ('sample-how-our-research-score-works', '[Sample] How our research score works',
     'Development sample article: a walkthrough of the six score categories, missing-data handling, and why the score is not a prediction.',
     'methodology', 'free', 'published', 'CryptoScope Research (sample)', now() - interval '2 days', '{methodology,sample}',
     '[{"name":"CryptoScope methodology page","url":null}]'),
    ('sample-reading-token-unlock-schedules', '[Sample] Reading token unlock schedules',
     'Development sample premium article: how to evaluate vesting and unlock events relative to circulating supply.',
     'tokenomics', 'premium', 'published', 'CryptoScope Research (sample)', now() - interval '1 day', '{tokenomics,unlocks,sample}', '[]')
  on conflict (slug) do nothing
  returning id, slug
)
insert into public.research_article_bodies (article_id, body_markdown)
select id, case slug
  when 'sample-how-our-research-score-works' then E'## Sample content\n\nThis is **development sample content**. Replace it with real research before launch.\n\n- Six weighted categories\n- Missing data is reported, not penalized\n- Scores are heuristics, not predictions'
  else E'## Sample premium content\n\nThis is **development sample content** visible only to Premium members.\n\n1. Compare unlock size to circulating supply\n2. Check the source and its confidence\n3. Consider the market-cap impact'
end
from a
on conflict (article_id) do nothing;
