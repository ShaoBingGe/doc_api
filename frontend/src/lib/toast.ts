/**
 * Lightweight toast notification system.
 * Uses a simple pub/sub pattern — no external deps.
 */

export type ToastType = 'success' | 'error' | 'info' | 'warning'

export interface Toast {
  id: string
  type: ToastType
  message: string
  duration?: number
}

type ToastListener = (toast: Toast) => void
type DismissListener = (id: string) => void

const listeners: ToastListener[] = []
const dismissListeners: DismissListener[] = []

let counter = 0

function show(message: string, type: ToastType = 'info', duration = 3500): string {
  const id = `toast-${++counter}`
  const toast: Toast = { id, type, message, duration }
  listeners.forEach((fn) => fn(toast))
  return id
}

function dismiss(id: string) {
  dismissListeners.forEach((fn) => fn(id))
}

export const toast = {
  success: (msg: string, duration?: number) => show(msg, 'success', duration),
  error: (msg: string, duration?: number) => show(msg, 'error', duration ?? 5000),
  info: (msg: string, duration?: number) => show(msg, 'info', duration),
  warning: (msg: string, duration?: number) => show(msg, 'warning', duration),
  dismiss,
}

export function onToast(fn: ToastListener) {
  listeners.push(fn)
  return () => {
    const i = listeners.indexOf(fn)
    if (i !== -1) listeners.splice(i, 1)
  }
}

export function onDismiss(fn: DismissListener) {
  dismissListeners.push(fn)
  return () => {
    const i = dismissListeners.indexOf(fn)
    if (i !== -1) dismissListeners.splice(i, 1)
  }
}
