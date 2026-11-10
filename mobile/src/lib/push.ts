import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import { Platform } from "react-native";
import { z } from "zod";
import type { createApi } from "./api";

const Ok = z.object({}).loose();

/** Ask for permission, get the Expo push token and register it with the server. Returns a user-facing status. */
export async function enablePush(api: ReturnType<typeof createApi>): Promise<{ ok: boolean; message: string; token?: string }> {
  if (!Device.isDevice) return { ok: false, message: "Push notifications need a physical device." };
  const current = await Notifications.getPermissionsAsync();
  const status = current.granted ? current : await Notifications.requestPermissionsAsync();
  if (!status.granted) return { ok: false, message: "Notifications are turned off for this app in system settings." };
  let token: string;
  try {
    token = (await Notifications.getExpoPushTokenAsync()).data;
  } catch {
    return { ok: false, message: "Could not get a push token (the app needs an EAS project id)." };
  }
  const res = await api.call("/api/devices", Ok, { method: "POST", body: { token, platform: Platform.OS === "ios" ? "ios" : "android" } });
  return res.ok ? { ok: true, message: "Notifications enabled on this device.", token } : { ok: false, message: res.error.message };
}

export async function disablePush(api: ReturnType<typeof createApi>, token: string) {
  return api.call("/api/devices", Ok, { method: "DELETE", body: { token } });
}
