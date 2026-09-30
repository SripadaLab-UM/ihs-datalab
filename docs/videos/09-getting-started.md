# Walkthrough: Getting started on a Mac

Status: **draft for review.**

| | |
|---|---|
| Subtitle | From the website to your first screen |
| Length | About 3:00 |
| For | Anyone installing DataLab on a Mac for the first time, or updating |
| Sources | The release website (datalab.cap-study.com, filmed live); Finder and Terminal on a Mac, recorded (a Downloads folder holding only the download; the real 0.3.0-beta.5 installer, with the user's name in paths shown as "you"); DataLab itself. Two moments are labelled illustrations: typing the key at the prompt (keys are never typed on camera), and DataLab's start window. |

## Rules for this video

- The installer's words are its own: the terminal view replays a real run.
- Keys and passwords never appear. The narration says where they come from
  (Aman's email), not what they are.
- Docker Desktop is explained as what DataLab runs on, not as DataLab.

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
  > This video shows how to install DataLab on a Mac. First, what to have
  > ready. Then, how to download DataLab and run its setup, and where your
  > key and password go. And last, how to open DataLab, and check it's
  > working.

---

### Chapter 2: Before you start

**Shot 2.1**

- **Visual (website):** The page, then Before you start.
  - `ready` · "Have these ready" · Before you start
  - `none` · "shared repositories"
- **Narration:**
  > Everything starts at the DataLab website. Have these ready: your
  > University of Michigan GPT Toolkit API key and the database password, both in the email from
  > Aman; access to the Michigan Medicine VPN; and your GitHub account, for
  > the lab's shared repositories.

---

### Chapter 3: Download and run setup

**Shot 3.1**

- **Visual (website):** Download for Mac.
  - `download` · "press Download for Mac" · Download for Mac
- **Narration:**
  > Under Install on Mac, press Download for Mac.

**Shot 3.2**

- **Visual (Finder, real):** Downloads, with the ZIP; double-clicked; the
  DataLab-Mac folder appears. (A folder holding only the download, so no
  one's own files are on screen.)
- **Narration:**
  > In your Downloads folder, double-click the ZIP. It unzips into a folder
  > called DataLab Mac. Then go back to the website.

**Shot 3.3**

- **Visual (website):** The command, and Copy.
  - `command` · "Copy the setup command" · The setup command
  - `copy` · "with the Copy button" · Copy
  - `none` · "Command and Space"
- **Narration:**
  > Copy the setup command with the Copy button. Then open Terminal: it's
  > in Applications, under Utilities, or search for it with Command and
  > Space.

**Shot 3.4**

- **Visual (Terminal, real):** The command pasted and run; the three
  downloads and their checks. (In all Terminal footage, the user's name in
  paths shows as "you".)
- **Narration:**
  > Paste the command and press Return. Setup downloads DataLab, checks
  > every file against the release, and keeps your settings if you've
  > installed it before.

**Shot 3.5**

- **Visual (Terminal, real):** Steps 1 to 4.
- **Narration:**
  > It then works through seven steps. Step one is Docker Desktop, which
  > DataLab runs on. If it isn't installed, setup offers to install it,
  > and Docker's own window asks you to accept its agreement: choose Use
  > recommended settings. Docker only needs to be running. It isn't
  > DataLab itself, so you won't find anything about the study inside it.
  > Steps two to four install DataLab and download what it needs.

---

### Chapter 4: Your key and password

**Shot 4.1**

- **Visual (terminal, real run, then a labelled illustration of typing):**
  Step 5's prompt; asterisks appear as a key is pasted.
  - term · "" · "U-M GPT API key:"
  - typed · "****************************************"
  - typed · "Saved the U-M GPT key to this computer's keychain."
  - typed · "Database password for SVC_IHS_AGENT: ****************"
  - typed · "Saved the database password to this computer's keychain."
- **Narration:**
  > Step five asks for your key, then your database password. Paste each
  > one from Aman's email and press Return. Each character shows as a
  > star, and both are saved in your Mac's Keychain, not in any file.

**Shot 4.2**

- **Visual (Terminal, real):** Steps 6 and 7, and Done.
- **Narration:**
  > Step six offers to sign you in to GitHub, for the lab's knowledge base
  > and pipelines. You can also do that later, in Settings. Step seven adds
  > DataLab to Applications and your Desktop. Setup is done.

---

### Chapter 5: Open DataLab

**Shot 5.1**

- **Visual (the Desktop, real):** The DataLab shortcut setup added.
  - `icon` · "the DataLab shortcut" · Double-click to open
  - `none` · "with Spotlight"
- **Narration:**
  > To open DataLab, double-click the DataLab shortcut on your Desktop.
  > It's also in your Applications folder, or search for it with
  > Spotlight.

**Shot 5.2**

- **Visual (illustration):** DataLab's own Terminal window starting.
  - window · "~ % '/Users/you/Library/Application Support/DataLab/app/bin/datalab' --profile real serve"
  - window · "DataLab (real) is starting. Open: http://127.0.0.1:8765/sign-in?token=…"
- **Narration:**
  > It opens a Terminal window of its own: leave that open while you work.
  > Then your browser opens on DataLab.

**Shot 5.3**

- **Visual (DataLab):** The Workspace, then the status buttons.
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
