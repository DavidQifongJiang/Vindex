import { useRef } from "react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle
} from "@/components/ui/alert-dialog";
import { buttonVariants } from "@/components/ui/button";

export function DeleteVideoDialog({
  onCancel,
  onConfirm,
  title
}: {
  onCancel: () => void;
  onConfirm: () => void;
  title: string | null;
}) {
  const returnFocus = useRef<HTMLElement | null>(null);
  return (
    <AlertDialog open={title !== null} onOpenChange={(open) => { if (!open) onCancel(); }}>
      <AlertDialogContent
        className="bg-card"
        onOpenAutoFocus={() => { returnFocus.current = document.activeElement as HTMLElement | null; }}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          if (returnFocus.current?.isConnected) returnFocus.current.focus();
        }}
      >
        <AlertDialogHeader>
          <AlertDialogTitle className="[overflow-wrap:anywhere]">Delete “{title}”?</AlertDialogTitle>
          <AlertDialogDescription>
            This removes the database row, stored files, metrics, transcript, and search vectors.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction className={buttonVariants({ variant: "destructive" })} onClick={onConfirm}>
            Delete video
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
