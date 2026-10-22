"""Who is using DataLab.

On a person's computer there is one user, the local one (id 1), and the
browser signs in with the launcher's one-time link (web.BrowserSession).

On the lab's server (`deployment = "server"`) people sign in with GitHub
(github_web.py): DataLab knows each one (users.py), keeps each browser's
sign-in (sessions.py), and their GitHub tokens, encrypted (tokens.py).
"""
