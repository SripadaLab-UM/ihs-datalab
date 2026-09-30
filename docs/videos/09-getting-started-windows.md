# Walkthrough: Getting started on Windows

Status: **draft for review.**

| | |
|---|---|
| Subtitle | From the website to your first screen |
| Length | About 3:00 |
| For | Anyone installing DataLab on a Windows PC for the first time, or updating |
| Sources | The release website (datalab.cap-study.com, filmed live); File Explorer, PowerShell and the Start menu on a Windows 10 PC, recorded (a Downloads folder holding only the download; a second run of the real 0.3.0-beta.5 installer, on a PC it had already set up, so it asks nothing secret, with the user's name in paths shown as "you"); DataLab itself. Three moments are labelled illustrations: what Step 1 asks the first time (administrator permission and a restart), typing the key at the prompt (keys are never typed on camera), and DataLab's own start window. |

## Rules for this video

- The installer's words are its own: the PowerShell footage is a real run,
  and the illustrations use its messages word for word.
- Keys and passwords never appear. The narration says where they come from
  (Aman's email), and where they go (the setup window, or later Settings and
  Safety), not what they are.
- Docker Desktop is explained as what DataLab runs on, not as DataLab.
- The Michigan Medicine profile page is never filmed (it shows the person's
  own account): only the website's link to it.
- "University of Michigan" in the narration, never "U-M" (Polly reads it as
  letters).

## Script and storyboard

---

### Chapter 1: This video covers

**Shot 1.1**

- **Visual:** The title and agenda.
  - agenda · Before you start · "what to have ready"
  - agenda · Download and run setup · "download DataLab"
  - agenda · Your key and password · "your key and password"
  - agenda · Open DataLab · "open DataLab"
- **Narration:**
  > This video shows how to install DataLab on a Windows PC. First, what to
  > have ready. Then, how to download DataLab and run its setup, and where
  > your key and password go. And last, how to open DataLab, and check it's
  > working.

---

### Chapter 2: Before you start

**Shot 2.1**

- **Visual (website):** The page, then Before you start: the whole card,
  then its privileged access link (the link only; the profile page isn't
  opened).
  - `ready` · "Have these ready" · Before you start
  - `privileged` · "activate privileged access" · Windows only
  - `ready` · "You'll also need" · Before you start
  - `none` · "shared repositories"
- **Narration:**
  > Everything starts at the DataLab website. Have these ready. On Windows,
  > first activate privileged access on your Michigan Medicine profile
  > page: setup needs administrator permission once. You'll also need your
  > University of Michigan GPT Toolkit API key and the database password,
  > both in the email from Aman; the University of Michigan VPN; and your
  > GitHub account, for the lab's shared repositories.

---

### Chapter 3: Download and run setup

**Shot 3.1**

- **Visual (website):** Download for Windows.
  - `download` · "press Download for Windows" · Download for Windows
- **Narration:**
  > Under Install on Windows, press Download for Windows.

**Shot 3.2**

- **Visual (File Explorer, real):** Downloads, with the ZIP; right-click,
  Extract All; the destination is Downloads; Extract; the DataLab-Windows
  folder appears. (A folder holding only the download, so no one's own
  files are on screen.)
- **Narration:**
  > In your Downloads folder, right-click the ZIP and choose Extract All.
  > Choose Downloads as the destination, and press Extract. Then go back to
  > the website.

**Shot 3.3**

- **Visual (website):** The command, and Copy.
  - `command` · "Copy the setup command" · The setup command
  - `copy` · "with the Copy button" · Copy
  - `none` · "type PowerShell"
- **Narration:**
  > Copy the setup command with the Copy button. Then open PowerShell:
  > press Start, type PowerShell, and press Enter.

**Shot 3.4**

- **Visual (PowerShell, real):** The command pasted and run; Step 1 begins.
  (In all PowerShell footage, the user's name in paths shows as "you".)
- **Narration:**
  > Paste the command and press Enter. Setup works through eight steps.
  > Step one gets Windows ready: DataLab runs in Docker, a sealed-off space
  > on your computer, and Docker needs a Windows feature called WSL.

**Shot 3.5**

- **Visual (PowerShell, then a labelled illustration of a first run):**
  what Step 1 lists and asks the first time.
  - term · "" · "called WSL."
  - typed · "   To do that, Windows needs to:"
  - typed · "     - Turn on WSL and install it (WSL 2.7.14, from Microsoft)"
  - typed · "     - Install Docker Desktop (from Docker)"
  - typed · "   This needs administrator permission, just this once."
  - typed · "   Ready to continue? [Y/n]"
- **Narration:**
  > The first time, setup lists what Windows needs and asks for
  > administrator permission. Press Enter, then click Yes when Windows
  > asks. Windows then needs a restart, and setup carries on by itself
  > after you sign in.

**Shot 3.6**

- **Visual (PowerShell, real):** Steps 1 to 5 on a PC that's ready.
- **Narration:**
  > Step two starts Docker Desktop. If it shows a welcome screen, you can
  > skip it. Docker only needs to be running: it isn't DataLab itself, so
  > you won't find anything about the study inside it. Steps three to five
  > install DataLab and download what it needs.

---

### Chapter 4: Your key and password

**Shot 4.1**

- **Visual (PowerShell, real run, then a labelled illustration of typing):**
  Step 6's instructions; the prompts, and asterisks as a key is pasted.
  - term · "" · "Credential Manager."
  - typed · "U-M GPT API key: ****************************************"
  - typed · "Saved the U-M GPT key to this computer's keychain."
  - typed · "Database password for SVC_IHS_AGENT: ****************"
  - typed · "Saved the database password to this computer's keychain."
- **Narration:**
  > Step six asks for your key, then your database password. Paste each
  > one from Aman's email and press Enter. Each character shows as a star,
  > and both are saved in Windows Credential Manager, not in any file. If
  > you skip them now, add them later in DataLab, under Settings and
  > Safety, then Connections.

**Shot 4.2**

- **Visual (PowerShell, real):** Steps 7 and 8, and All done.
- **Narration:**
  > Step seven offers to sign you in to GitHub, for the lab's knowledge
  > base and pipelines; you can also do that later, in Settings. Step eight
  > adds DataLab to the Start menu and your Desktop. Setup is done.

---

### Chapter 5: Open DataLab

**Shot 5.1**

- **Visual (the Start menu, real):** Start, DataLab typed, the entry setup
  added.
  - `entry` · "type DataLab" · DataLab in the Start menu
  - `none` · "on your Desktop"
- **Narration:**
  > To open DataLab, press Start, type DataLab, and press Enter. There's
  > also a DataLab shortcut on your Desktop.

**Shot 5.2**

- **Visual (illustration):** DataLab's own PowerShell window starting.
  - window · "DataLab (real) is starting. Open: http://127.0.0.1:8765/sign-in?token=…"
- **Narration:**
  > It opens a PowerShell window of its own: leave that open while you
  > work. Then your browser opens on DataLab.

**Shot 5.3**

- **Visual (DataLab):** The Workspace, then the status buttons (the Mac
  video's shot, if DataLab looks the same).
  - `workspace` · "This is DataLab" · DataLab
  - `status` · "the buttons at the top right" · Is it set up?
  - `none` · "Getting connected"
- **Narration:**
  > This is DataLab. Connect to the VPN, and check the buttons at the top
  > right. If the key or password is missing, or you skipped it, add it in
  > Settings and Safety, under Connections. The video Getting connected
  > shows how.

**Shot 5.4**

- **Visual:** The end card.
- **Narration:**
  > That's DataLab installed. There's more on the website, under A little
  > help getting started.

---

## Open items

- The website's Before you start card says "U-M VPN access"; the Mac video
  says "Michigan Medicine VPN". This one follows the website.
- The key illustration leaves out the real prompt's "✓ Received N
  characters." line, as the Mac video does.
