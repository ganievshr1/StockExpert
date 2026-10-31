import type { ReactNode } from "react";

/** Keeps wide and long tables readable without widening the surrounding page. */
export function ScrollArea({
  label,
  className = "",
  children,
}: {
  label: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={`table-wrap ${className}`.trim()}
      role="region"
      aria-label={label}
      tabIndex={0}
    >
      {children}
    </div>
  );
}
