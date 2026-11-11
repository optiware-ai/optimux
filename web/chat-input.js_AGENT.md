# chat-input.js Agent Details

## File Overview
`web/chat-input.js` (v0.8) holds the chat box's typing helpers as plain functions, so Node can test them (`tests/test_chat_input.py`). Loaded before `app.js` as `window.OptimuxChatInput`; under Node it is `module.exports`.

## Core Functionality
- `listEnter(value, start, end)`: for Shift+Enter. In a list line (`1.` or `1)` up to 6 digits, or `-`, `*`, `•`, then a space) it returns `{value, cursor}` with the next item (`2.` keeps the indent and the `.` or `)`; a bullet repeats). Text after the cursor moves to the new item; spaces around the split are dropped. On an empty item it removes the marker (the line is plain again). `null` means "a normal newline": not a list line, a selection, or the cursor inside the marker.
- `listSpace(value, start, end)`: for the space key. When the text from the line start to the cursor is just a marker (`1.`, `12)`, `-`, `*`, `•`), it returns the line with two spaces before the marker and a space after it, so the item shows indented in the box (two spaces keep it a list for Markdown). `null` elsewhere (inside a sentence, already indented, a selection).
- `cleanForSend(text)`: drops trailing empty items ("3. ") and trailing space.
- `userMarkdown(text)`: a line that is only `` `text` `` (outside a fenced block) becomes a ```term block; inline backticks stay inline.

## Notes
- Pure string functions: no DOM, no Vue. `app.js` sets the textarea's value and cursor.
- Listed in `main.PAGE_FILES`, so a change offers the open pages a reload.
