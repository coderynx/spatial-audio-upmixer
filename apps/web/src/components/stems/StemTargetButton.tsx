import { Check } from "lucide-react";
import { getStemIcon } from "@/lib/stems";
import { cn } from "@/lib/utils";

export const stemBorderClasses: Record<string, string> = {
  vocals: "border-rose-300/80 dark:border-rose-800",
  bass: "border-teal-300/80 dark:border-teal-800",
  drums: "border-orange-300/80 dark:border-orange-800",
  kick: "border-red-300/80 dark:border-red-800",
  snare: "border-pink-300/80 dark:border-pink-800",
  toms: "border-lime-300/80 dark:border-lime-800",
  guitar: "border-emerald-300/80 dark:border-emerald-800",
  piano: "border-violet-300/80 dark:border-violet-800",
  "hi-hat": "border-yellow-300/80 dark:border-yellow-800",
  ride: "border-cyan-300/80 dark:border-cyan-800",
  crash: "border-sky-300/80 dark:border-sky-800",
  crowd: "border-blue-300/80 dark:border-blue-800",
  "lead vocals": "border-rose-300/80 dark:border-rose-800",
  "backing vocals": "border-fuchsia-300/80 dark:border-fuchsia-800",
  other: "border-slate-300/80 dark:border-slate-700",
};

export const stemActiveClasses: Record<string, string> = {
  vocals: "border-rose-500 bg-rose-500/10 text-rose-700 dark:text-rose-300",
  bass: "border-teal-500 bg-teal-500/10 text-teal-700 dark:text-teal-300",
  drums:
    "border-orange-500 bg-orange-500/10 text-orange-700 dark:text-orange-300",
  kick: "border-red-500 bg-red-500/10 text-red-700 dark:text-red-300",
  snare: "border-pink-500 bg-pink-500/10 text-pink-700 dark:text-pink-300",
  toms: "border-lime-500 bg-lime-500/10 text-lime-700 dark:text-lime-300",
  guitar:
    "border-emerald-500 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  piano:
    "border-violet-500 bg-violet-500/10 text-violet-700 dark:text-violet-300",
  "hi-hat":
    "border-yellow-500 bg-yellow-500/10 text-yellow-700 dark:text-yellow-300",
  ride: "border-cyan-500 bg-cyan-500/10 text-cyan-700 dark:text-cyan-300",
  crash: "border-sky-500 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  crowd: "border-blue-500 bg-blue-500/10 text-blue-700 dark:text-blue-300",
  "lead vocals":
    "border-rose-500 bg-rose-500/10 text-rose-700 dark:text-rose-300",
  "backing vocals":
    "border-fuchsia-500 bg-fuchsia-500/10 text-fuchsia-700 dark:text-fuchsia-300",
  other: "border-slate-500 bg-slate-500/10 text-slate-700 dark:text-slate-300",
};

export function stemToggleKey(stem: string) {
  return stem.toLowerCase();
}

export function StemTargetButton({
  stem,
  active,
  onClick,
  className,
}: {
  stem: string;
  active: boolean;
  onClick: () => void;
  className?: string;
}) {
  const stemKey = stemToggleKey(stem);
  const StemIcon = getStemIcon(stem);
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "flex h-9 min-w-0 items-center justify-between gap-2 rounded-md border px-3 text-sm transition-colors",
        stemBorderClasses[stemKey] || stemBorderClasses.other,
        active
          ? stemActiveClasses[stemKey] || stemActiveClasses.other
          : "hover:bg-muted",
        className,
      )}
    >
      <span className="flex min-w-0 items-center gap-2 text-left">
        <StemIcon className="h-4 w-4 shrink-0" aria-hidden="true" />
        <span className="truncate">{stem}</span>
      </span>
      {active && <Check className="h-4 w-4 shrink-0" />}
    </button>
  );
}
