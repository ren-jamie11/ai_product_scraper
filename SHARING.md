# Sharing the app with the team

The app runs on one computer (the host PC, `JAMIE`). Everyone else opens it in their browser
from a shortcut on the shared drive. Teammates install nothing.

The project folder stays on the host PC. Do **not** copy it to the shared drive: `config.py`
holds the API keys and `amazon_cookies.*.txt` holds the Amazon login. Only
`Product Research.url` goes on the shared drive.

## One-time setup on the host PC

1. Right-click `Setup host (run once as administrator).bat` → **Run as administrator**.
   It adds one Windows Firewall rule so office PCs can reach port 5000.
2. Settings → System → Power → Screen and sleep → set "When plugged in, put my device to
   sleep after" to **Never**. A sleeping PC takes the app offline.
3. Copy `Product Research.url` to the shared drive.
4. Optional, to start the app whenever you log in: press `Win + R`, type `shell:startup`,
   and put a shortcut to `Start server.bat` in the folder that opens.

## Every day

- **Host:** double-click `Start server.bat` and leave the black window open. Closing it stops
  the app for everyone. On the host PC itself, open `http://127.0.0.1:5000`.
- **Team:** double-click `Product Research.url` on the shared drive.

`python app.py` on its own still runs the app privately on the host PC, as before.

## What the team should know

- Everyone works in the same workspace: the same groups, runs, results and Settings.
  A Settings change by one person applies to everyone.
- Extract and Parse spend the shared API credits.
- Extractions run one at a time. If someone else is extracting, adding listings or fetching
  reviews, yours shows "Waiting for another extraction to finish…" and starts by itself when
  theirs ends. Leave the page open. Parsing does not wait in this line.
- If two people edit the same run at the same moment, the last save wins.
- A progress bar shows only for the person who started the job. Others see the result after
  reloading the page.
- Amazon sign-in can only be done on the host PC. If reviews stop arriving, the host signs in
  from Settings, then anyone can press **Fetch reviews**.

## If the shortcut doesn't open

| What you see | Fix |
|---|---|
| "This site can't be reached" on every PC | `Start server.bat` isn't running on the host, or the host is asleep. |
| Works on the host, not on other PCs | Run the setup file again as administrator. Check the other PC is on the office network, not guest Wi-Fi. |
| Other PCs can't find `JAMIE` | Use the host's IP address instead: on the host run `ipconfig`, note the IPv4 Address, and edit the shortcut to `http://<that address>:5000/`. Reserve that address for the host in the router so it doesn't change. |
| The host's network is marked "Public" | Settings → Network & internet → Ethernet → set the network profile to **Private**. The firewall rule already covers both, but Private is the right setting for an office network. |
