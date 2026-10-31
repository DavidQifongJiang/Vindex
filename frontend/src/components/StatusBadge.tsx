import { CircleAlert, CircleCheck, Clock3, LoaderCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { readableStatus, statusTone } from "@/lib/format";

const toneStyles = {
  good: "bg-good text-good-foreground",
  active: "bg-active text-active-foreground",
  bad: "bg-bad text-bad-foreground",
  idle: "bg-muted text-muted-foreground"
};

export function StatusBadge({ label, value }: { label?: string; value?: string | null }) {
  const tone = statusTone(value);
  const Icon = tone === "good" ? CircleCheck : tone === "bad" ? CircleAlert : tone === "active" ? LoaderCircle : Clock3;

  return (
    <Badge className={cn("gap-1 rounded-full border-transparent px-2 font-medium whitespace-nowrap shadow-none", toneStyles[tone])}>
      <Icon className={cn("size-3", tone === "active" && "animate-spin motion-reduce:animate-none")} aria-hidden="true" />
      {label ? `${label}: ` : ""}{readableStatus(value)}
    </Badge>
  );
}
