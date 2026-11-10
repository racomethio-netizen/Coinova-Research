import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { colors } from "@/components/ui";
import { SessionProvider } from "@/lib/session";

export default function RootLayout() {
  return (
    <SessionProvider>
      <StatusBar style="light" />
      <Stack screenOptions={{ headerStyle: { backgroundColor: colors.elevated }, headerTintColor: colors.fg, contentStyle: { backgroundColor: colors.bg } }}>
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen name="sign-in" options={{ title: "Sign in" }} />
        <Stack.Screen name="signals/[id]" options={{ title: "Signal" }} />
      </Stack>
    </SessionProvider>
  );
}
