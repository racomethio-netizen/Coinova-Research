-- =============================================================================
-- 0007 NEXUS tiers (part 1): add the `pro` membership tier.
--
-- Kept alone in its own migration because a new enum value cannot be used in
-- the same transaction that adds it. Part 2 (0008) uses it.
-- =============================================================================

alter type public.membership_tier add value if not exists 'pro' before 'premium';
