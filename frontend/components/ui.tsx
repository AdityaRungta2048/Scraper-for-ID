import type { ReactNode } from "react";

import type { DisplayDecision } from "@/lib/format";

type Tone = "neutral" | "info" | "success" | "warning" | "danger";

const TONES: Record<Tone, string> = {
  neutral: "bg-zinc-100 text-zinc-700 ring-zinc-200",
  info: "bg-sky-50 text-sky-700 ring-sky-200",
  success: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  warning: "bg-amber-50 text-amber-800 ring-amber-200",
  danger: "bg-rose-50 text-rose-700 ring-rose-200",
};

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${TONES[tone]}`}>
      {children}
    </span>
  );
}

const DECISION_TONE: Record<DisplayDecision, Tone> = {
  MATCH: "success",
  REVIEW: "warning",
  NO_MATCH: "neutral",
  NOT_FOUND: "neutral",
  ERROR: "danger",
  SKIPPED: "neutral",
  PENDING: "info",
};

export function DecisionBadge({ decision }: { decision: DisplayDecision }) {
  return <Badge tone={DECISION_TONE[decision]}>{decision.replace("_", " ")}</Badge>;
}

export function Card({ children, className = "", ...rest }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={`rounded-xl border border-zinc-200 bg-white shadow-sm ${className}`} {...rest}>
      {children}
    </div>
  );
}

export function ProgressBar({ value }: { value: number }) {
  return (
    <div
      className="h-3 w-full overflow-hidden rounded-full bg-zinc-100"
      role="progressbar"
      aria-valuenow={value}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div className="h-full rounded-full bg-brand-600 transition-all duration-500" style={{ width: `${value}%` }} />
    </div>
  );
}

export function Stat({ label, value, tone }: { label: string; value: ReactNode; tone?: Tone }) {
  const color =
    tone === "success"
      ? "text-emerald-700"
      : tone === "warning"
        ? "text-amber-700"
        : tone === "danger"
          ? "text-rose-700"
          : "text-zinc-900";
  return (
    <div className="rounded-lg border border-zinc-200 bg-white px-4 py-3">
      <div className="text-xs font-medium uppercase tracking-wide text-zinc-500">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${color}`}>{value}</div>
    </div>
  );
}

type ButtonVariant = "primary" | "secondary" | "danger" | "ghost";
const VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-brand-600 text-white hover:bg-brand-700 disabled:bg-zinc-300",
  secondary: "bg-white text-zinc-800 ring-1 ring-inset ring-zinc-300 hover:bg-zinc-50 disabled:text-zinc-400",
  danger: "bg-white text-rose-700 ring-1 ring-inset ring-rose-300 hover:bg-rose-50",
  ghost: "text-zinc-600 hover:bg-zinc-100",
};

export function Button({
  variant = "secondary",
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  return (
    <button
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition disabled:cursor-not-allowed ${VARIANTS[variant]} ${className}`}
      {...props}
    />
  );
}

export function LinkButton({
  variant = "secondary",
  className = "",
  ...props
}: React.AnchorHTMLAttributes<HTMLAnchorElement> & { variant?: ButtonVariant }) {
  return (
    <a
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition ${VARIANTS[variant]} ${className}`}
      {...props}
    />
  );
}

export function Alert({ tone = "info", title, children }: { tone?: Tone; title?: string; children?: ReactNode }) {
  return (
    <div className={`rounded-lg px-4 py-3 text-sm ring-1 ring-inset ${TONES[tone]}`} role="alert">
      {title && <div className="font-semibold">{title}</div>}
      {children && <div className={title ? "mt-1" : ""}>{children}</div>}
    </div>
  );
}
