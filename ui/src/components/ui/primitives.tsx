import { forwardRef, type ButtonHTMLAttributes, type HTMLAttributes, type ReactNode } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/cn";

const badgeVariants = cva(
  "inline-flex max-w-full items-center gap-1 whitespace-nowrap rounded-md border px-1.5 py-0.5 text-[11px] font-semibold leading-4 tracking-wide",
  {
    variants: {
      tone: {
        neutral: "border-ink-600 bg-ink-800 text-zinc-300",
        fraud: "border-red-500/40 bg-red-500/15 text-red-300",
        legit: "border-emerald-500/40 bg-emerald-500/15 text-emerald-300",
        uncertain: "border-amber-400/40 bg-amber-400/15 text-amber-200",
        accent: "border-tg-500/50 bg-tg-500/15 text-tg-300",
        info: "border-sky-500/40 bg-sky-500/10 text-sky-300",
        purple: "border-violet-500/40 bg-violet-500/15 text-violet-300",
        muted: "border-ink-600 bg-transparent text-zinc-500",
        danger: "border-red-500 bg-red-600 text-white",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export type BadgeTone = NonNullable<VariantProps<typeof badgeVariants>["tone"]>;

export function Badge({ tone, className, ...props }: HTMLAttributes<HTMLSpanElement> & { tone?: BadgeTone }) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 rounded-lg border text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "border-ink-600 bg-ink-800 text-zinc-200 hover:border-ink-500 hover:bg-ink-700",
        primary: "border-tg-500 bg-tg-500 text-ink-950 hover:bg-tg-400",
        ghost: "border-transparent bg-transparent text-zinc-400 hover:bg-ink-800 hover:text-zinc-100",
      },
      size: { sm: "h-8 px-2.5 text-[13px]", md: "h-9 px-3.5" },
    },
    defaultVariants: { variant: "default", size: "md" },
  },
);

export const Button = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonVariants>
>(function Button({ className, variant, size, type = "button", ...props }, ref) {
  return <button ref={ref} type={type} className={cn(buttonVariants({ variant, size }), className)} {...props} />;
});

export function Panel({
  title,
  icon,
  actions,
  children,
  className,
  bodyClassName,
  id,
}: {
  title?: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  id?: string;
}) {
  const headingId = id ? `${id}-title` : undefined;
  return (
    <section className={cn("panel", className)} aria-labelledby={title ? headingId : undefined} id={id}>
      {title !== undefined && (
        <header className="panel-header">
          <h2 id={headingId} className="panel-title flex items-center gap-2">
            {icon}
            {title}
          </h2>
          {actions}
        </header>
      )}
      <div className={cn("p-4", bodyClassName)}>{children}</div>
    </section>
  );
}

export function EmptyState({ icon, title, children, className }: { icon?: ReactNode; title: ReactNode; children?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-ink-600 px-6 py-10 text-center", className)}>
      {icon && <div className="text-zinc-500">{icon}</div>}
      <p className="text-sm font-semibold text-zinc-200">{title}</p>
      {children && <div className="max-w-xl text-sm text-zinc-400">{children}</div>}
    </div>
  );
}

export function Kv({ label, children, className }: { label: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={cn("min-w-0", className)}>
      <dt className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">{label}</dt>
      <dd className="mt-0.5 min-w-0 text-sm text-zinc-100">{children}</dd>
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-md bg-ink-800", className)} aria-hidden="true" />;
}
