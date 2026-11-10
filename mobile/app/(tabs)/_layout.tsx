import { Redirect, Tabs } from "expo-router";
import { colors, Loading } from "@/components/ui";
import { useSession } from "@/lib/session";

export default function TabsLayout() {
  const { session, loading } = useSession();
  if (loading) return <Loading />;
  if (!session) return <Redirect href="/sign-in" />;
  return (
    <Tabs screenOptions={{
      headerStyle: { backgroundColor: colors.elevated }, headerTintColor: colors.fg,
      tabBarStyle: { backgroundColor: colors.elevated, borderTopColor: colors.line }, tabBarActiveTintColor: colors.accent, tabBarInactiveTintColor: colors.muted,
    }}>
      <Tabs.Screen name="index" options={{ title: "Home" }} />
      <Tabs.Screen name="scanner" options={{ title: "Scanner" }} />
      <Tabs.Screen name="signals" options={{ title: "Signals" }} />
      <Tabs.Screen name="performance" options={{ title: "Performance" }} />
      <Tabs.Screen name="paper" options={{ title: "Paper" }} />
      <Tabs.Screen name="settings" options={{ title: "Settings" }} />
    </Tabs>
  );
}
