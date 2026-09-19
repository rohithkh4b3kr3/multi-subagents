---
description: Save a short handoff note so you can /clear (or start a new session) without losing your place
---

The conversation has grown, and every request re-sends all of it. Write a handoff so the work can continue in a fresh, small conversation.

1. Create the folder `~/.claude/handoffs/` if needed, and write the note to `~/.claude/handoffs/<name>.md`, where `<name>` is the current project folder name (lowercase, spaces replaced by `-`). Overwrite any earlier note for this project. Do not write it inside the project itself.
2. Keep it under about 40 lines and make it self-contained. Use these sections, and leave a section out if it is empty:
   - **Goal**: what we are trying to achieve, in one or two sentences.
   - **Decisions**: choices already made and the reason, so they are not re-litigated.
   - **State**: what is done and verified, what is half-done, what is broken. Exact file paths and the commands to run or test.
   - **Next steps**: the next three or fewer concrete actions.
   - **Do not redo**: dead ends already ruled out.
   Facts only: no narration, no praise. Never include secrets, tokens, passwords or personal data.
3. Then reply with exactly two short lines: the path of the note, and this instruction for the user:
   `Run /clear, then start with: read ~/.claude/handoffs/<name>.md and continue`

Do not clear the conversation yourself and do not start new work.
