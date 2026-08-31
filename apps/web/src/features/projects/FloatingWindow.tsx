import * as React from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

export function FloatingWindow({ title, ariaLabel, trigger, children, icon, borderColor }: {
  title: string;
  ariaLabel: string;
  trigger: (open: () => void, expanded: boolean) => React.ReactNode;
  children: React.ReactNode;
  icon?: React.ReactNode;
  borderColor?: string;
}) {
  const [open, setOpen] = React.useState(false);
  const [position, setPosition] = React.useState<{ left: number; top: number } | null>(null);
  const windowRef = React.useRef<HTMLDivElement>(null);
  const dragRef = React.useRef<{ pointerId: number; offsetX: number; offsetY: number } | null>(null);
  React.useEffect(() => { if (open) windowRef.current?.focus(); }, [open]);
  const place = (left: number, top: number) => {
    const rect = windowRef.current?.getBoundingClientRect();
    if (!rect) return;
    setPosition({ left: Math.min(window.innerWidth - 64, Math.max(64 - rect.width, left)), top: Math.min(window.innerHeight - 32, Math.max(0, top)) });
  };
  return <>{trigger(() => setOpen(true), open)}{open && createPortal(
    <div className="pointer-events-none fixed inset-0 z-50"><div ref={windowRef} role="dialog" aria-modal="false" aria-label={ariaLabel} tabIndex={-1}
      className="pointer-events-auto fixed max-h-[calc(100vh-24px)] w-[420px] max-w-[calc(100vw-24px)] overflow-auto rounded-lg border bg-card shadow-2xl outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
      style={{ ...(position ?? { left: "50%", top: "50%", transform: "translate(-50%, -50%)" }), ...(borderColor ? { borderColor: `${borderColor}40` } : {}) }} onKeyDown={(event) => { if (event.key === "Escape") setOpen(false); }}>
      <div tabIndex={0} aria-label={`Move ${title} window`} className="flex h-8 touch-none cursor-default items-center border-b px-3 text-[12px] font-semibold outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/60"
        onPointerDown={(event) => { if (event.button !== 0 || !windowRef.current) return; const rect = windowRef.current.getBoundingClientRect(); dragRef.current = { pointerId: event.pointerId, offsetX: event.clientX - rect.left, offsetY: event.clientY - rect.top }; event.currentTarget.setPointerCapture(event.pointerId); setPosition({ left: rect.left, top: rect.top }); }}
        onPointerMove={(event) => { const drag = dragRef.current; if (drag?.pointerId === event.pointerId) place(event.clientX - drag.offsetX, event.clientY - drag.offsetY); }}
        onPointerUp={(event) => { if (dragRef.current?.pointerId !== event.pointerId) return; dragRef.current = null; event.currentTarget.releasePointerCapture(event.pointerId); }}>
        {icon}<span className="min-w-0 flex-1 truncate">{title}</span><button type="button" aria-label={`Close ${ariaLabel}`} className="-mr-1 rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60" onPointerDown={(event) => event.stopPropagation()} onClick={() => setOpen(false)}><X className="h-3.5 w-3.5" /></button>
      </div><div className="p-3">{children}</div>
    </div></div>, document.body)}</>;
}
