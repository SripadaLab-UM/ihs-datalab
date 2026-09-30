# Walkthrough: Settings & Safety

Status: **draft for review.**

| | |
|---|---|
| Subtitle | The connections the AI works through, and how to check they're safe |
| Length | About 1:50 |
| Leads with | The connections the AI uses (U-M GPT and the study database), then the Safety check |
| Help guide | [Settings & Safety](../guide/settings.md) |
| Filmed on | The showcase practice DataLab (synthetic data), 1920×1080 |

How the spotlight cues work: see [12-sql-playground.md](12-sql-playground.md).

## Script and storyboard

---

### Chapter 1: This video covers

**Shot 1.1**

- **Visual:** The title and agenda.
  - agenda · The AI's connections · "the connections the AI works through"
  - agenda · The Safety check · "the Safety check"
  - agenda · Export folders · "your export folders"
  - agenda · Updates, storage and diagnostics · "updates, storage"
- **Narration:**
  > This video walks through Settings and Safety. First, the connections
  > the AI works through. Then, the Safety check, and your export folders.
  > And last, updates, storage, and what to send when something goes
  > wrong.

---

### Chapter 2: The AI's connections

**Shot 2.1**

- **Visual:** Connections: U-M GPT and the study database.
  - `gpt` · "the University of Michigan's" · The AI model service
  - `database` · "and the study database" · The study database
  - `none` · "never sees them"
- **Narration:**
  > The AI works through two connections: the University of Michigan's
  > approved GPT service, and the study database. Their key and password are
  > kept in your computer's keychain. You can save or replace them here,
  > but DataLab never shows them again, and the AI never sees them.

**Shot 2.2**

- **Visual:** The database's read-only roles; Test connection pressed.
  - `roles` · "only the read-only roles" · Read-only roles
- **Narration:**
  > The database connection switches on only the read-only roles, so the AI
  > can read the data but never change it. Test connection checks each one.

---

### Chapter 3: The Safety check

**Shot 3.1**

- **Visual:** Safety: Run safety check pressed; checking (sped up); "Every
  check passed".
  - `run` · "Run safety check" · Run safety check
  - `result` · "every promise" · The result
- **Narration:**
  > The Safety check tests DataLab's promises live. Press Run safety check,
  > and it starts sealed test sessions and tries to break out of them. In
  > about twenty seconds, it reports on every promise.

**Shot 3.2**

- **Visual:** Details opened: the checks, grouped by promise.
  - `details` · "Details lists" · Every check
  - `none` · "can't write"
- **Narration:**
  > Details lists every check, grouped by promise: that a session can't reach
  > the web, that no key is inside it, that the database connection can't
  > write, and more.

---

### Chapter 4: Export folders

**Shot 4.1**

- **Visual:** Export folders: the section for Dropbox and other folders.
  - `dropbox` · "You choose them" · Your own folders
  - `none` · "workflows deliver to"
- **Narration:**
  > Export folders are the only places results can go. You choose them,
  > such as a Dropbox folder, and the destinations workflows deliver to.

---

### Chapter 5: Updates, storage and diagnostics

**Shot 5.1**

- **Visual:** Updates, then Storage.
  - `updates` · "a newer DataLab" · Updates
  - `storage` · "Storage shows" · Storage
  - `none` · "deleted automatically"
- **Narration:**
  > Updates tells you when a newer DataLab is out, and keeps a backup of
  > its database before each update. Storage shows what DataLab keeps, and
  > lets you remove old results one at a time. Nothing is deleted
  > automatically.

**Shot 5.2**

- **Visual:** About: Copy diagnostics.
  - `diagnostics` · "Copy diagnostics" · Copy diagnostics
- **Narration:**
  > And if something goes wrong, About has Copy diagnostics: a report for
  > the maintainer that never includes your keys, SQL, results, or
  > conversations.

**Shot 5.3**

- **Visual:** The end card.
- **Narration:**
  > That's Settings and Safety. There's more in Help, under Settings and
  > Safety.
