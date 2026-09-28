// Evidence objects are immutable. Keep their images shared between history entries.
export function createAnnotationHistory(limit=50) {
  const past = [], future = [];
  return {
    record(before, after) {
      if (before.annotations.length === after.annotations.length &&
          before.annotations.every((mark, i) => mark === after.annotations[i]) &&
          before.dynamicSnapshots.length === after.dynamicSnapshots.length &&
          before.dynamicSnapshots.every((moment, i) => moment === after.dynamicSnapshots[i]) &&
          before.snapshot === after.snapshot) return;
      past.push({before, after});
      if (past.length > limit) past.shift();
      future.length = 0;
    },
    undo(apply) {
      const entry = past.at(-1);
      if (!entry || !apply(entry.after, entry.before)) return false;
      past.pop(); future.push(entry); return true;
    },
    redo(apply) {
      const entry = future.at(-1);
      if (!entry || !apply(entry.before, entry.after)) return false;
      future.pop(); past.push(entry); return true;
    },
    clear() { past.length = 0; future.length = 0; },
    get canUndo() { return past.length > 0; },
    get canRedo() { return future.length > 0; }
  };
}
