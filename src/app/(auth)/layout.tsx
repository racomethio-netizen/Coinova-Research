import { Container } from "@/components/ui";
import { isSupabaseConfigured } from "@/lib/env";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <Container className="flex justify-center py-12 sm:py-16">
      <div className="w-full max-w-md rounded-lg border border-line bg-elevated p-6 sm:p-8">
        {!isSupabaseConfigured() ? (
          <p role="status" className="mb-5 rounded-md border border-warning/40 bg-warning-soft px-3 py-2 text-xs text-warning">
            Authentication is not configured on this server (SUPABASE_URL / SUPABASE_ANON_KEY). Sign-in and registration are unavailable until it is.
          </p>
        ) : null}
        {children}
      </div>
    </Container>
  );
}
