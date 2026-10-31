import type { FormEventHandler, InputHTMLAttributes, ReactNode } from "react";
import { Search } from "lucide-react";

export function SearchBar({
  children,
  inputProps,
  onSubmit
}: {
  children: ReactNode;
  inputProps: InputHTMLAttributes<HTMLInputElement>;
  onSubmit: FormEventHandler<HTMLFormElement>;
}) {
  return (
    <form
      className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 rounded-[10px] border border-input bg-card py-1.5 pr-1.5 pl-3 shadow-sm focus-within:ring-2 focus-within:ring-ring/40"
      role="search"
      onSubmit={onSubmit}
    >
      <Search className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      <input
        className="h-9 min-w-0 flex-[1_1_140px] bg-transparent text-sm outline-none placeholder:text-muted-foreground"
        type="search"
        {...inputProps}
      />
      {children}
    </form>
  );
}
