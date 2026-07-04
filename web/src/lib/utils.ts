import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge conditional class names, de-duplicating conflicting Tailwind utilities.
 * The `cn(...)` helper the shadcn-derived components expect (see
 * components/ui/tooltip.tsx, components/assistant-ui/context-display.tsx). */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
