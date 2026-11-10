/**
 * Pure MFA helpers (no framework imports, unit-tested).
 *
 * Privileged roles (admin, editor) take effect only in an MFA-verified
 * session (`aal2`); the database enforces this in is_admin()/is_staff()
 * (migration 0009). This mirror lets the app send privileged users to
 * enrollment/verification instead of showing "forbidden".
 */
export const PRIVILEGED_ROLES = ["admin", "editor"] as const;

export function mfaState(roles: readonly string[], aal: unknown) {
  const privileged = roles.some((r) => (PRIVILEGED_ROLES as readonly string[]).includes(r));
  return { privileged, mfaRequired: privileged && aal !== "aal2" };
}
