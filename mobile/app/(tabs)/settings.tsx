import * as SecureStore from "expo-secure-store";
import * as WebBrowser from "expo-web-browser";
import { useEffect, useState } from "react";
import { TextInput } from "react-native";
import { z } from "zod";
import { Button, Card, colors, H1, Screen, T } from "@/components/ui";
import { config } from "@/lib/config";
import { disablePush, enablePush } from "@/lib/push";
import { useSession } from "@/lib/session";
import { supabase } from "@/lib/supabase";

const PUSH_KEY = "nexus.push_token";
const Any = z.object({}).loose();

export default function Settings() {
  const { session, api } = useSession();
  const [pushToken, setPushToken] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [confirm, setConfirm] = useState("");
  useEffect(() => { void SecureStore.getItemAsync(PUSH_KEY).then(setPushToken); }, []);

  async function signOut() {
    if (pushToken) {
      await disablePush(api, pushToken); // stop notifications for this device before signing out
      await SecureStore.deleteItemAsync(PUSH_KEY);
    }
    await supabase.auth.signOut();
  }

  return (
    <Screen>
      <H1>Settings</H1>
      <Card><T tone="muted">Signed in as</T><T>{session?.user.email}</T></Card>
      <Card>
        <T style={{ fontWeight: "700" }}>Notifications</T>
        <T tone="muted">Receive your alerts on this device. Choose “Mobile push” on an alert to use it.</T>
        {pushToken ? (
          <Button variant="secondary" label="Turn off on this device" onPress={async () => { await disablePush(api, pushToken); await SecureStore.deleteItemAsync(PUSH_KEY); setPushToken(null); setMsg("Notifications turned off on this device."); }} />
        ) : (
          <Button label="Enable notifications" onPress={async () => { const r = await enablePush(api); setMsg(r.message); if (r.ok && r.token) { await SecureStore.setItemAsync(PUSH_KEY, r.token); setPushToken(r.token); } }} />
        )}
        {msg ? <T tone="muted">{msg}</T> : null}
      </Card>
      <Card>
        <T style={{ fontWeight: "700" }}>Account</T>
        <Button variant="secondary" label="Security, data export and more (website)" onPress={() => WebBrowser.openBrowserAsync(`${config.apiUrl}/dashboard/settings`)} />
        <Button variant="secondary" label="Sign out" onPress={signOut} />
      </Card>
      <Card style={{ borderColor: colors.negative }}>
        <T style={{ fontWeight: "700" }}>Delete account</T>
        <T tone="muted">Permanently deletes your account and data. Payment records are kept without your details, as required for financial record-keeping. Type DELETE to confirm.</T>
        <TextInput accessibilityLabel="Type DELETE to confirm" autoCapitalize="characters" value={confirm} onChangeText={setConfirm}
          style={{ backgroundColor: colors.subtle, color: colors.fg, borderColor: colors.line, borderWidth: 1, borderRadius: 8, padding: 10, minHeight: 44 }} />
        <Button variant="danger" label="Delete my account" disabled={confirm !== "DELETE"} onPress={async () => {
          const r = await api.call("/api/account", Any, { method: "DELETE", body: { confirm: "DELETE" } });
          if (!r.ok) return setMsg(r.error.message);
          await SecureStore.deleteItemAsync(PUSH_KEY);
          await supabase.auth.signOut();
        }} />
      </Card>
    </Screen>
  );
}
