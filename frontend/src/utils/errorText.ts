// "Couldn't update the case. Something went wrong on our side. Try again..." as its first sentence (what failed)
// and the rest (what the officer can do).
export function splitMessage(message: string): [string, string | null] {
  const match = /^(.+?[.!?])\s+(\S[\s\S]*)$/.exec(message.trim());
  return match ? [match[1], match[2]] : [message, null];
}
