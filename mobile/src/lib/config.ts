/** Public build-time configuration (EXPO_PUBLIC_* values ship inside the app; never secrets). */
export const config = {
  apiUrl: (process.env.EXPO_PUBLIC_API_URL ?? "").replace(/\/+$/, ""),
  supabaseUrl: process.env.EXPO_PUBLIC_SUPABASE_URL ?? "",
  supabaseAnonKey: process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY ?? "",
};

export const isConfigured = () => Boolean(config.apiUrl && config.supabaseUrl && config.supabaseAnonKey);
