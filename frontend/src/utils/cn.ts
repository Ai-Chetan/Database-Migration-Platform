import { clsx, type ClassValue } from 'clsx'

/** Combine conditional class names. */
export function cn(...inputs: ClassValue[]) {
  return clsx(inputs)
}
