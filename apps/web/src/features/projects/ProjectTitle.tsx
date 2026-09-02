import * as React from "react";
import { Check, Pencil, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export function ProjectTitle({
  name,
  onRename,
  isTauri,
  entity = "project",
  onClick,
}: {
  name: string;
  onRename: (name: string) => void;
  isTauri: boolean;
  entity?: string;
  onClick?: () => void;
}) {
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState(name);
  const inputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  const cancel = () => {
    setDraft(name);
    setEditing(false);
  };
  const confirm = () => {
    const next = draft.trim();
    if (next && next !== name) onRename(next);
    cancel();
  };

  if (editing)
    return (
      <div
        className="flex min-w-0 items-center gap-1"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <Input
          ref={inputRef}
          aria-label={`${entity[0].toUpperCase()}${entity.slice(1)} name`}
          className={cn(
            "h-7 min-w-0 max-w-56 font-semibold",
            isTauri && "bg-card",
          )}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") confirm();
            if (event.key === "Escape") cancel();
          }}
        />
        <Button
          variant={isTauri ? "secondary" : "ghost"}
          size="icon"
          className="h-6 w-6"
          aria-label={`Confirm ${entity} name`}
          onClick={confirm}
        >
          <Check />
        </Button>
        <Button
          variant={isTauri ? "secondary" : "ghost"}
          size="icon"
          className="h-6 w-6"
          aria-label={`Cancel ${entity} name`}
          onClick={cancel}
        >
          <X />
        </Button>
      </div>
    );

  return (
    <div
      className="group flex min-w-0 items-center gap-0.5"
      onMouseDown={(event) => event.stopPropagation()}
      onClick={onClick}
    >
      <span className="truncate text-[13px] font-semibold">{name}</span>
      <Button
        variant={isTauri ? "secondary" : "ghost"}
        size="icon"
        className={cn(
          "h-6 w-6 shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100",
          isTauri && "bg-secondary/80",
        )}
        aria-label={`Rename ${entity}`}
        onClick={(event) => {
          event.stopPropagation();
          setDraft(name);
          setEditing(true);
        }}
      >
        <Pencil />
      </Button>
    </div>
  );
}
