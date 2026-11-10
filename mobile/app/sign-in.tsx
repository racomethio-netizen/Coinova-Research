import { router } from "expo-router";
import * as WebBrowser from "expo-web-browser";
import { useState } from "react";
import { TextInput } from "react-native";
import { Button, Card, colors, ErrorNote, H1, Screen, T } from "@/components/ui";
import { config, isConfigured } from "@/lib/config";
import { supabase } from "@/lib/supabase";

const input = { backgroundColor: colors.subtle, color: colors.fg, borderColor: colors.line, borderWidth: 1, borderRadius: 8, padding: 12, minHeight: 44 } as const;

export default function SignIn() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!isConfigured()) return <Screen><ErrorNote message="This build is not configured with a server. Set EXPO_PUBLIC_API_URL and the Supabase public values." /></Screen>;
  return (
    <Screen>
      <H1>Sign in</H1>
      <Card>
        <T tone="muted">Email</T>
        <TextInput accessibilityLabel="Email" autoCapitalize="none" autoComplete="email" keyboardType="email-address" value={email} onChangeText={setEmail} style={input} />
        <T tone="muted">Password</T>
        <TextInput accessibilityLabel="Password" secureTextEntry autoComplete="password" value={password} onChangeText={setPassword} style={input} />
        {error ? <T tone="negative">{error}</T> : null}
        <Button label={busy ? "Signing in…" : "Sign in"} disabled={busy || !email || !password} onPress={async () => {
          setBusy(true);
          setError(null);
          const { error: e } = await supabase.auth.signInWithPassword({ email: email.trim(), password });
          setBusy(false);
          // Generic message: never reveal whether an account exists.
          if (e) setError("Sign-in failed. Check your email and password, and confirm your email address if you have just registered.");
          else router.replace("/");
        }} />
      </Card>
      <Button variant="secondary" label="Create an account (website)" onPress={() => WebBrowser.openBrowserAsync(`${config.apiUrl}/register`)} />
      <Button variant="secondary" label="Forgot password (website)" onPress={() => WebBrowser.openBrowserAsync(`${config.apiUrl}/forgot-password`)} />
    </Screen>
  );
}
