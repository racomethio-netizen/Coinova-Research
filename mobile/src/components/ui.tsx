import type { ReactNode } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View, type TextStyle, type ViewStyle } from "react-native";

/** Navy theme, mirroring the web design tokens (AA contrast). */
export const colors = {
  bg: "#07101e", elevated: "#0c1829", subtle: "#112137", line: "#1c3050", fg: "#e8eef7", muted: "#a6b6cc", faint: "#8195b1",
  accent: "#5aaaff", accentFg: "#04101f", positive: "#3fd197", negative: "#ff7070", warning: "#f2c14e",
};

export function Screen({ children, refreshControl }: { children: ReactNode; refreshControl?: React.ComponentProps<typeof ScrollView>["refreshControl"] }) {
  return <ScrollView style={{ flex: 1, backgroundColor: colors.bg }} contentContainerStyle={{ padding: 16, gap: 12 }} refreshControl={refreshControl}>{children}</ScrollView>;
}

export function Card({ children, style }: { children: ReactNode; style?: ViewStyle }) {
  return <View style={[styles.card, style]}>{children}</View>;
}

export function H1({ children }: { children: ReactNode }) {
  return <Text accessibilityRole="header" style={styles.h1}>{children}</Text>;
}

export function T({ children, tone = "fg", style }: { children: ReactNode; tone?: keyof typeof colors; style?: TextStyle }) {
  return <Text style={[{ color: colors[tone], fontSize: 14, lineHeight: 20 }, style]}>{children}</Text>;
}

export function Badge({ label, tone = "accent" }: { label: string; tone?: "accent" | "positive" | "negative" | "warning" | "faint" }) {
  return <View style={[styles.badge, { borderColor: colors[tone] }]}><Text style={{ color: colors[tone], fontSize: 12, fontWeight: "600" }}>{label}</Text></View>;
}

export function Button({ label, onPress, disabled, variant = "primary" }: { label: string; onPress: () => void; disabled?: boolean; variant?: "primary" | "secondary" | "danger" }) {
  const bg = variant === "primary" ? colors.accent : variant === "danger" ? colors.negative : colors.subtle;
  const fg = variant === "secondary" ? colors.fg : colors.accentFg;
  return (
    <Pressable accessibilityRole="button" accessibilityState={{ disabled: !!disabled }} onPress={onPress} disabled={disabled}
      style={({ pressed }) => [styles.button, { backgroundColor: bg, opacity: disabled ? 0.5 : pressed ? 0.85 : 1 }]}>
      <Text style={{ color: fg, fontWeight: "600" }}>{label}</Text>
    </Pressable>
  );
}

export function Loading() {
  return <ActivityIndicator accessibilityLabel="Loading" color={colors.accent} style={{ marginTop: 24 }} />;
}

export function ErrorNote({ message }: { message: string }) {
  return <Card style={{ borderColor: colors.negative }}><T tone="negative">{message}</T></Card>;
}

export function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <View style={{ flexDirection: "row", justifyContent: "space-between", gap: 12, paddingVertical: 4 }}>
      <T tone="faint">{label}</T>
      {typeof value === "string" ? <T style={{ fontVariant: ["tabular-nums"], flexShrink: 1, textAlign: "right" }}>{value}</T> : value}
    </View>
  );
}

export function Disclaimer() {
  return <T tone="faint" style={{ fontSize: 12, marginTop: 8 }}>Research and paper trading only. Not financial advice. Past results, including backtests, do not indicate future results. Crypto assets are volatile and you can lose money.</T>;
}

const styles = StyleSheet.create({
  card: { backgroundColor: colors.elevated, borderColor: colors.line, borderWidth: 1, borderRadius: 10, padding: 14, gap: 6 },
  h1: { color: colors.fg, fontSize: 22, fontWeight: "700" },
  badge: { borderWidth: 1, borderRadius: 999, paddingHorizontal: 8, paddingVertical: 2, alignSelf: "flex-start" },
  button: { minHeight: 44, borderRadius: 8, paddingHorizontal: 16, alignItems: "center", justifyContent: "center" },
});
