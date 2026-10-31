import { Globe, Lock } from "lucide-react";

export function VisibilityIcon({ visibility }: { visibility?: string | null }) {
  const isPublic = visibility === "public";
  const Icon = isPublic ? Globe : Lock;
  const label = isPublic ? "Public" : "Private";

  return (
    <span className="inline-flex text-muted-foreground" role="img" title={label} aria-label={label}>
      <Icon className="size-3.5" aria-hidden="true" />
    </span>
  );
}
