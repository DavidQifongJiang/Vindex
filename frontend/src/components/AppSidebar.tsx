import { useState } from "react";
import { Compass, Library, LogOut, ScanSearch, UploadCloud } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { initials } from "@/lib/format";
import { isDarkTheme, setDarkTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { AUTH_ENABLED, signIn, signOut } from "../auth";
import type { UserProfile } from "../types";

type FeedMode = "library" | "public";

export function AppSidebar({
  activeFeed,
  currentUser,
  hasSession,
  isAuthLoading,
  isSignedIn,
  onSelectFeed,
  onUpload
}: {
  activeFeed: FeedMode | null;
  currentUser: UserProfile | null;
  hasSession: boolean;
  isAuthLoading: boolean;
  isSignedIn: boolean;
  onSelectFeed: (feed: FeedMode) => void;
  onUpload: () => void;
}) {
  const [dark, setDark] = useState(isDarkTheme);
  const navItems = [
    { feed: "library" as const, label: "My library", Icon: Library },
    { feed: "public" as const, label: "Public explore", Icon: Compass }
  ];

  return (
    <aside className="flex flex-col gap-4 border-b bg-card p-4 md:sticky md:top-0 md:h-screen md:overflow-y-auto md:border-r md:border-b-0">
      <div className="flex items-center gap-2.5">
        <span className="grid size-[34px] place-items-center rounded-lg bg-primary text-primary-foreground">
          <ScanSearch className="size-4" aria-hidden="true" />
        </span>
        <div className="leading-tight">
          <strong className="block text-[15px] font-semibold">Vindex</strong>
          <small className="text-xs text-muted-foreground">Video Semantic Search</small>
        </div>
      </div>

      <Button className="w-full" disabled={!isSignedIn} onClick={onUpload}>
        <UploadCloud aria-hidden="true" />
        Upload video
      </Button>

      <nav className="grid grid-cols-2 gap-0.5 md:grid-cols-1" aria-label="Video feed">
        {navItems.map(({ feed, label, Icon }) => (
          <button
            aria-current={activeFeed === feed ? "page" : undefined}
            className={cn(
              "flex items-center justify-center gap-2.5 rounded-md px-2.5 py-2 text-sm font-medium hover:bg-accent focus-visible:ring-1 focus-visible:ring-ring focus-visible:outline-none md:justify-start",
              activeFeed === feed && "bg-selected hover:bg-selected"
            )}
            key={feed}
            type="button"
            onClick={() => onSelectFeed(feed)}
          >
            <Icon className="size-4 shrink-0" aria-hidden="true" />
            {label}
          </button>
        ))}
      </nav>

      <div className="hidden flex-1 md:block" />

      <label className="flex items-center justify-between gap-2 px-0.5 text-sm">
        Dark theme
        <Switch
          checked={dark}
          onCheckedChange={(checked) => {
            setDarkTheme(checked);
            setDark(checked);
          }}
        />
      </label>

      <div className="flex items-center gap-2.5 border-t pt-3">
        {currentUser?.picture_url ? (
          <img alt="" className="size-8 shrink-0 rounded-full object-cover" src={currentUser.picture_url} />
        ) : (
          <span className="grid size-8 shrink-0 place-items-center rounded-full bg-muted text-xs font-semibold">{initials(currentUser)}</span>
        )}
        <div className="min-w-0 flex-1 leading-tight">
          <strong className="block truncate font-medium">{currentUser?.name || currentUser?.email || "Guest"}</strong>
          <small className="text-xs text-muted-foreground">{AUTH_ENABLED ? "Cognito profile" : "Local dev profile"}</small>
        </div>
        {AUTH_ENABLED && (
          hasSession ? (
            <Button aria-label="Sign out" size="icon" title="Sign out" variant="ghost" onClick={signOut}>
              <LogOut aria-hidden="true" />
            </Button>
          ) : (
            <Button disabled={isAuthLoading} size="sm" variant="ghost" onClick={() => void signIn()}>
              Sign in
            </Button>
          )
        )}
      </div>
    </aside>
  );
}
