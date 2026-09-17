import { useBlocker } from "@tanstack/react-router"
import { useCallback, useEffect, useRef, useState } from "react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { getAccessToken } from "@/lib/auth"

const exitEvent = "amfv-editor-exit"

/** Run intentional logout only after the active editor accepts departure. */
export function requestEditorExit(action: () => void) {
  const event = new CustomEvent(exitEvent, { cancelable: true, detail: action })
  if (window.dispatchEvent(event)) action()
}

export function useEditorExit(dirty: boolean, uncertain = false) {
  const sessionToken = useRef(getAccessToken())
  const protectedRef = useRef(false)
  protectedRef.current = dirty || uncertain
  const [pendingAction, setPendingAction] = useState<(() => void) | null>(null)
  const shouldBlockFn = useCallback(
    () =>
      protectedRef.current &&
      sessionToken.current !== null &&
      getAccessToken() === sessionToken.current,
    [],
  )
  const blocker = useBlocker({
    shouldBlockFn,
    withResolver: true,
    enableBeforeUnload: () =>
      protectedRef.current &&
      sessionToken.current !== null &&
      getAccessToken() === sessionToken.current,
  })
  const confirmAction = useCallback((action: () => void) => {
    if (protectedRef.current) setPendingAction(() => action)
    else action()
  }, [])
  useEffect(() => {
    const listener = (event: Event) => {
      if (!protectedRef.current) return
      event.preventDefault()
      confirmAction((event as CustomEvent<() => void>).detail)
    }
    window.addEventListener(exitEvent, listener)
    return () => window.removeEventListener(exitEvent, listener)
  }, [confirmAction])
  const stay = () => {
    setPendingAction(null)
    blocker.reset?.()
  }
  const dialog = (
    <Dialog
      open={blocker.status === "blocked" || pendingAction !== null}
      onOpenChange={(open) => {
        if (!open) stay()
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Leave unfinished work?</DialogTitle>
          <DialogDescription>
            {uncertain
              ? "A save is pending or its outcome is unknown. Leaving loses this local recovery copy. Stay to check save status, retry, or download your copy."
              : "Your unsaved changes will be lost. Stay to keep editing, or discard them and leave."}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={stay}>
            Stay
          </Button>
          <Button
            onClick={() => {
              const action = pendingAction
              setPendingAction(null)
              if (action) action()
              else blocker.proceed?.()
            }}
          >
            Discard and leave
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
  return { dialog, confirmAction }
}
