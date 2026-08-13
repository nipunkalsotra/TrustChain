import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// `catch (e: unknown)` is the type-safe form — this extracts a message
// without needing `any` at every call site.
export function errorMessage(e: unknown, fallback: string): string {
  return e instanceof Error ? e.message : fallback
}
