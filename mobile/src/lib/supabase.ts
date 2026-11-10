import "react-native-url-polyfill/auto";
import { createClient } from "@supabase/supabase-js";
import * as SecureStore from "expo-secure-store";
import { chunkedStorage } from "./chunked-storage";
import { config } from "./config";

/** Auth only: sessions live in the device's secure storage (Keychain / Keystore), never AsyncStorage. */
const storage = chunkedStorage({
  getItem: (k) => SecureStore.getItemAsync(k),
  setItem: (k, v) => SecureStore.setItemAsync(k, v),
  removeItem: (k) => SecureStore.deleteItemAsync(k),
});

export const supabase = createClient(config.supabaseUrl || "https://not-configured.invalid", config.supabaseAnonKey || "not-configured", {
  auth: { storage, autoRefreshToken: true, persistSession: true, detectSessionInUrl: false },
});
