import { useRef } from "react";
import { LoaderCircle, UploadCloud } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { filenameToTitle } from "@/lib/format";
import { NativeSelect } from "./NativeSelect";

type VideoVisibility = "private" | "public";

export function UploadDialog({
  file,
  isUploading,
  message,
  onFileChange,
  onOpenChange,
  onTitleChange,
  onUpload,
  onVisibilityChange,
  open,
  title,
  visibility
}: {
  file: File | null;
  isUploading: boolean;
  message: string;
  onFileChange: (file: File | null) => void;
  onOpenChange: (open: boolean) => void;
  onTitleChange: (title: string) => void;
  onUpload: () => void;
  onVisibilityChange: (visibility: VideoVisibility) => void;
  open: boolean;
  title: string;
  visibility: VideoVisibility;
}) {
  const returnFocus = useRef<HTMLElement | null>(null);
  return (
    <Dialog open={open} onOpenChange={(nextOpen) => { if (!isUploading) onOpenChange(nextOpen); }}>
      <DialogContent
        className="max-h-[calc(100vh-32px)] overflow-y-auto bg-card sm:max-w-[480px]"
        onOpenAutoFocus={() => { returnFocus.current = document.activeElement as HTMLElement | null; }}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          if (returnFocus.current?.isConnected) returnFocus.current.focus();
        }}
      >
        <DialogHeader>
          <DialogTitle>Upload video</DialogTitle>
          <DialogDescription>Vindex will create a searchable transcript.</DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            onUpload();
          }}
        >
          <label className="grid cursor-pointer justify-items-center gap-1.5 rounded-lg border border-dashed border-input px-3 py-6 text-center hover:bg-accent focus-within:ring-2 focus-within:ring-ring/40">
            <input
              accept="video/*"
              className="sr-only"
              type="file"
              disabled={isUploading}
              onChange={(event) => {
                const nextFile = event.target.files?.[0] ?? null;
                onFileChange(nextFile);
                if (nextFile && !title.trim()) onTitleChange(filenameToTitle(nextFile.name));
              }}
            />
            <UploadCloud className="size-4" aria-hidden="true" />
            <span className="[overflow-wrap:anywhere]">{file ? file.name : "Choose video file"}</span>
          </label>
          <label className="grid gap-1.5 font-medium">
            Title
            <Input
              className="font-normal"
              placeholder="Name this video"
              disabled={isUploading}
              value={title}
              onChange={(event) => onTitleChange(event.target.value)}
            />
          </label>
          <label className="grid gap-1.5 font-medium">
            Visibility
            <NativeSelect
              className="h-9 max-w-none border border-input bg-card px-2.5 font-normal"
              value={visibility}
              disabled={isUploading}
              onChange={(event) => onVisibilityChange(event.target.value as VideoVisibility)}
            >
              <option value="private">Private</option>
              <option value="public">Public</option>
            </NativeSelect>
          </label>
          {message && <p className="text-bad-foreground" role="alert">{message}</p>}
          <DialogFooter className="gap-2 sm:gap-2">
            <Button disabled={isUploading} type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button disabled={isUploading || !file} type="submit">
              {isUploading ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <UploadCloud aria-hidden="true" />}
              {isUploading ? "Uploading" : "Upload video"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
