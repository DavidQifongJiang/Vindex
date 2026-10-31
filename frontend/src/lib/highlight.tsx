import type { ReactNode } from "react";
import type { SearchAlgorithm } from "../types";

const STOP_WORDS = new Set("a an and are at be by do does for from how i in is it of on or our so that the this to we what when with you your".split(" "));

export function highlightPattern(query: string, algorithm: SearchAlgorithm) {
  if (algorithm === "exact") return query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return (query.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [])
    .filter((word) => !STOP_WORDS.has(word))
    // English words match on their stem so "crashes" also marks "crash"; other scripts match literally.
    .map((word) => /^[a-z0-9]+$/.test(word) ? `(?<![\\p{L}\\p{N}])${word.slice(0, 5)}[\\p{L}\\p{N}]*` : word)
    .join("|");
}

export function highlight(text: string, pattern: string): ReactNode {
  if (!pattern) return text;
  return text
    .split(new RegExp(`(${pattern})`, "giu"))
    .map((part, index) => (index % 2 ? <mark key={index}>{part}</mark> : part));
}
