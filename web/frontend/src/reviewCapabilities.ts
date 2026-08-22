/** The server capability snapshot is authoritative for every review command. */
export function isReviewActionAllowed<Action extends string>(
  allowedActions: readonly Action[] | undefined,
  action: Action,
): boolean {
  return allowedActions?.includes(action) ?? false
}

export function retrievalReviewAction(
  acceptAsGold: boolean | null,
): "accept" | "reject" | null {
  if (acceptAsGold === null) {
    return null
  }
  return acceptAsGold ? "accept" : "reject"
}
