/* Test double for DSH platform components; Desktop validates the real primitive. */
import React, { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'

export function Tooltip({ children }: { children: React.ReactNode }) { return <>{children}</> }
export function IconShareOutlineRegular() { return <svg aria-hidden="true" /> }
export function IconCheckCircleOutlineRegular() { return <svg aria-hidden="true" /> }
export function IconChevronLeftOutlineRegular() { return <svg aria-hidden="true" /> }
export function IconClockOutlineRegular() { return <svg aria-hidden="true" /> }
export function MenuItemButton({ children, disabled, disabledReason, onSelect }: {
  children: React.ReactNode; disabled?: boolean; disabledReason?: string; onSelect(): void
}) {
  return <button role="menuitem" aria-disabled={disabled || undefined} title={disabled ? disabledReason : undefined}
    onClick={disabled ? undefined : onSelect}>{children}</button>
}
export function Menu({ open, anchor, children, onClose, side, portal, autoFocus, anchorRef, listClassName }: {
  open: boolean; anchor: React.ReactNode; children?: React.ReactNode; onClose(): void;
  side?: string; portal?: boolean; autoFocus?: boolean; anchorRef?: React.RefObject<HTMLElement>; listClassName?: string
}) {
  const wrapper = useRef<HTMLSpanElement>(null)
  const list = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    if (list.current && anchorRef?.current) list.current.dataset.triggerAnchor = anchorRef.current.getAttribute('aria-label') ?? ''
    if (autoFocus) list.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus()
    const outside = (event: Event) => {
      if (event.target instanceof Node && !wrapper.current?.contains(event.target) && !list.current?.contains(event.target)) onClose()
    }
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onClose(); return }
      if (event.key === 'Tab' && event.shiftKey) { event.preventDefault(); onClose(); return }
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
      const items = [...(list.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? [])]
      if (!items.length) return
      const index = items.indexOf(document.activeElement as HTMLButtonElement)
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1
        : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length
      event.preventDefault(); items[next].focus()
    }
    document.addEventListener('pointerdown', outside); document.addEventListener('keydown', key)
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', key) }
  }, [open, onClose, autoFocus, anchorRef])
  const menu = open ? <div ref={list} role="menu" data-side={side} data-portal={portal} data-trigger-anchor={anchorRef?.current?.getAttribute('aria-label')} className={listClassName}>{children}</div> : null
  return <span ref={wrapper}>{anchor}{portal && menu ? createPortal(menu, document.body) : menu}</span>
}
