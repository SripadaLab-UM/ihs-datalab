# Walkthrough: Getting connected

Status: **draft for review.** Covers what [14-settings.md](14-settings.md)
did, on the real DataLab, plus troubleshooting; it can replace that video.

| | |
|---|---|
| Subtitle | The VPN, the status buttons, testing and fixing connections, and GitHub |
| Length | About 3:00 |
| Leads with | The connections the AI depends on, and how to check them |
| Help guide | [Settings & Safety](../guide/settings.md) |
| Filmed on | DataLab 0.2.0 beta 5 with the real settings, on its own empty data folder. Only Settings, the header and Help are filmed (enforced by the filming helper); the database host, account and role names are blurred. Keys and passwords are never typed on screen. Shots 4.2 and 6.3 need the VPN on; the rest are filmed with it off. |

How the spotlight cues work: see [12-sql-playground.md](12-sql-playground.md).

## Script and storyboard

---

### Chapter 1: This video covers

**Shot 1.1**

- **Visual:** The title and agenda.
  - agenda · Before you start · "before you start"
  - agenda · The status buttons · "the status buttons"
  - agenda · Test and fix a connection · "fix a connection"
  - agenda · GitHub, and other settings · "GitHub"
- **Narration:**
  > This video shows how to get DataLab connected, and what to do when it
  > won't connect. First, what to check before you start, and the status
  > buttons at the top right. Then, how to test and fix a connection. And
  > last, signing in to GitHub, and the other settings worth knowing.

---

### Chapter 2: Before you start

**Shot 2.1**

- **Visual:** DataLab, just opened.
  - `header` · "from its launcher" · DataLab
  - `none` · "only works once"
- **Narration:**
  > Docker Desktop must be running, and DataLab opens from its launcher.
  > If a page says you're signed out, start DataLab again from the
  > launcher: a sign-in link only works once.

**Shot 2.2**

- **Visual:** The header's status buttons.
  - `status` · "connect to it first" · The VPN, first
  - `none` · "before you start"
- **Narration:**
  > The study database is only reachable over the Michigan Medicine VPN,
  > so connect to it first, every time, before you start.

---

### Chapter 3: The status buttons

**Shot 3.1**

- **Visual:** Each status button opened in turn.
  - `db` · "The first is the database" · The database
  - `gpt` · "the AI model service's key" · The AI model service
  - `github` · "Then GitHub" · GitHub
  - `folders` · "your export folders" · Export folders
  - `none` · "links to its settings"
- **Narration:**
  > At the top right, a row of buttons shows how DataLab is set up. The
  > first is the database: open it to see its state, or test it. Next, the
  > AI model service's key. Then GitHub, and your export folders. Each one
  > links to its settings.

**Shot 3.2**

- **Visual:** Help; the More menu; the Session menu.
  - `help` · "Help opens" · Help
  - `more` · "The More menu" · More
  - `session` · "Session" · Session
  - `none` · "in this browser"
- **Narration:**
  > Help opens the guide for the screen you're on. The More menu switches
  > between light and dark, and sends feedback to the DataLab team. And
  > Session ends DataLab's sign-in in this browser.

---

### Chapter 4: Test and fix a connection

**Shot 4.1**

- **Visual:** Settings → Connections; the database's Test connection
  pressed with the VPN off; the error.
  - `dbtest` · "press Test connection" · Test connection
  - `dberror` · "can't reach the database" · Connect to the VPN
- **Narration:**
  > In Settings and Safety, under Connections, press Test connection. With
  > the VPN off, DataLab says it can't reach the database, and asks you to
  > connect to the VPN, then test again.

**Shot 4.2**

- **Visual (VPN on):** Test connection again; it passes.
  - `dbtest` · "test again" · The study database
  - `dbresult` · "read-only roles" · Connected, read-only
  - `none` · "switched on"
- **Narration:**
  > Connect to the VPN and test again. DataLab checks the database, and
  > that only its read-only roles are switched on.

**Shot 4.3**

- **Visual:** The AI model service's Test connection; then Replace key
  opened and cancelled.
  - `gpttest` · "Test the AI model service" · Test the AI model service
  - `gptresult` · "accepted" · Key accepted
  - `gptkey` · "press Replace key" · Replace key
  - `none` · "never shows it again"
- **Narration:**
  > Test the AI model service too: DataLab says whether the key was
  > accepted, and how many approved models it offers. If you're given a
  > new key, press Replace key and paste it. It goes straight to your
  > computer's keychain, and DataLab never shows it again.

**Shot 4.4**

- **Visual:** The database's Replace password.
  - `dbpass` · "Replace password" · Replace password
  - `none` · "the same way"
- **Narration:**
  > The database password works the same way, with Replace password.

---

### Chapter 5: GitHub

**Shot 5.1**

- **Visual:** The GitHub section: signed in; the lab's repos with Sync.
  - `github` · "Sign in to GitHub" · GitHub sign-in
  - `repos` · "the lab's knowledge base" · The lab's repos
  - `none` · "Sign out"
- **Narration:**
  > Sign in to GitHub to use the lab's knowledge base and pipelines. Once
  > you're signed in, Sync downloads the lab's knowledge base and pipelines
  > repos, and keeps them up to date. Switch account and Sign out are here
  > too.

---

### Chapter 6: Other settings

**Shot 6.1**

- **Visual:** Export folders: Add a folder, Choose in Dropbox.
  - `add` · "Add a folder" · Add a folder
  - `none` · "or key to set up"
- **Narration:**
  > Results leave DataLab only when you export them, and only to folders
  > you add here. Add a folder, such as one inside your Dropbox folder:
  > the Dropbox app uploads it, with no account or key to set up.

**Shot 6.2**

- **Visual:** Updates, then Storage.
  - `updates` · "Updates tells you" · Updates
  - `storage` · "Storage shows" · Storage
  - `none` · "deleted automatically"
- **Narration:**
  > Updates tells you when a newer DataLab is out: press Check now. Storage
  > shows what DataLab keeps, and lets you remove old results. Nothing is
  > deleted automatically.

**Shot 6.3**

- **Visual (VPN on):** Safety → Run safety check (sped up); then About →
  Copy diagnostics.
  - `safety` · "then, under About" · The Safety check
  - `diagnostics` · "Copy diagnostics" · Copy diagnostics
  - `none` · "or conversations"
- **Narration:**
  > Still stuck? Run the Safety check, then, under About, press Copy
  > diagnostics and send the report to the DataLab team. It never includes
  > your keys, SQL, results, or conversations.

**Shot 6.4**

- **Visual:** The end card.
- **Narration:**
  > That's getting DataLab connected. There's more in Help, under Settings
  > and Safety.
