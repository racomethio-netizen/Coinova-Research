import {
  Code2,
  Compass,
  Layers,
  LayoutTemplate,
  Plug,
  Smartphone,
  Sparkles,
  type LucideProps,
} from "lucide-react";

const icons = {
  code: Code2,
  layout: LayoutTemplate,
  sparkles: Sparkles,
  layers: Layers,
  plug: Plug,
  compass: Compass,
  smartphone: Smartphone,
} as const;

export type IconName = keyof typeof icons;

/** Named icon from the shared set, so content files can reference icons by key. */
export function Icon({ name, ...props }: { name: IconName } & LucideProps) {
  const Component = icons[name];
  return <Component aria-hidden="true" strokeWidth={1.6} {...props} />;
}
