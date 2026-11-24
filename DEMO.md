# Demo guide

A script, a checklist and a troubleshooting table for showing the app to other people. Everything here
uses the three sample documents in `samples/`, so there is nothing confidential on screen.

## Before you present

**The day before** (Gemini's free plan limits how much text you can index per day, so don't leave this
to the last minute):

1. Start the app (`Launch Study Assistant.bat`) and sign in as the administrator.
2. Load the samples. In a terminal in the project folder: `python -m app.ingest samples/*.pdf`
   (about 17 sections, a minute or two). Confirm all three documents appear on the left.
3. Rehearse the script below once, start to finish, and note how long answers take on your connection.
4. Don't use up the day's allowance on other experiments. Limits reset at about midnight Pacific time.

**Ten minutes before:**

- Launch the app and sign in. Keep the black launcher window open (closing it stops the app).
- Ask one question to wake everything up. The first answer is sometimes slower.
- Close other windows and apps, set the browser to full screen, and zoom in (`Ctrl` and `+`) so the back
  row can read it.
- Have a short screen recording of a good run ready as a backup, in case the internet or Google's
  service is having a bad day.

## A five-minute walk-through

| Step | Do this | What to say |
|---|---|---|
| 1 | Show the **sign-in screen**, then sign in. | "Each person has their own account. Passwords are stored as salted hashes, repeated wrong guesses lock the account, and sessions expire." |
| 2 | Point at the **document list** (left) and the note under it. | "These are my documents, private to my account. Nobody else who signs in can see them or get answers from them." |
| 3 | Ask: **What does error code E-47 mean?** | "It answers only from my documents and tells me where it found the answer." Point at the **source tag** (router manual, page 5). Mention that it found an exact code, not just a similar topic. |
| 4 | Follow up: **What should I do about it?** | "It remembers the conversation, so I can ask naturally." (Unplug, move somewhere cooler, wait 15 minutes.) |
| 5 | Ask: **How much notice do I need to request vacation?** | "Same app, a different document: this is an employee handbook. It searches all my documents at once." (Two weeks, page 2.) |
| 6 | Ask: **What does a double star before a pointer mean?** | "And the same tool works for studying: these are my C notes." (A pointer to a pointer, page 3.) |
| 7 | Ask: **What is the CEO's salary?** | "When the answer isn't in my documents it says so instead of guessing." ("I can't find that in the uploaded material.") |
| 8 | Open the account menu, then **Users and activity**. Show the user list and the **Activity log**. | "An administrator can add and remove people and see what happened: sign-ins, uploads, questions asked. It records that a question was asked, never what it said." |
| 9 | (Optional) **Add documents** and drop in a tiny PDF. | "Big files are indexed in the background with a progress bar. A 300-page manual takes about five minutes on the free plan." Don't upload anything large live. |

## Questions that are known to work

Checked against the sample documents; each answer cites the page shown. Rehearse them yourself before
you present, since the exact wording of an answer changes a little every time.

| Document | Question | Expected answer |
|---|---|---|
| Router manual | What does a solid red Power light mean? | A fault (page 2) |
| Router manual | What does error code E-47 mean? | The router overheated; unplug it, move it somewhere cooler, wait 15 minutes (page 5) |
| Router manual | How do I factory reset the router? | Hold the Reset button for 10 seconds until the Power light blinks red (page 4) |
| Router manual | How do I connect a device using WPS? | Hold the WPS button 3 seconds, then press WPS on the device (page 3) |
| Router manual | How long is the warranty? | Two years (page 6) |
| Handbook | How much notice do I need to request vacation? | Two weeks (page 2) |
| Handbook | How many sick days do I get, and when do I need a doctor's note? | 10 days; note after more than three days in a row (page 2) |
| Handbook | What is the meal allowance when travelling? | $60 per day, no alcohol (page 4) |
| Handbook | How soon must I report a phishing email? | Within one hour (page 5) |
| C notes | What does a double star before a pointer mean? | A pointer to a pointer (page 3) |
| C notes | What is a double free? | Freeing the same pointer twice; corrupts the heap (page 4) |
| (none of them) | What is the CEO's salary? | "I can't find that in the uploaded material." |
| (none of them) | What is the price of the AirLink X200? | Says it can't find it |

A broad question such as "What is this material about?" works but may mention only some of the
documents, so prefer specific questions in front of an audience.

## If someone asks how it works

- **It finds, then answers.** Each document is split into sections and turned into numbers that capture
  meaning. A question is turned into numbers the same way, the closest sections are found, and only
  those are given to the AI to write the answer. That is why every answer can point to a page.
- **It refuses to guess.** If nothing is close enough to the question, it says so without asking the AI.
- **Why it is slow sometimes.** Answers come from Google's Gemini service, which is shared and
  sometimes busy. The app times out slow requests and switches to a backup model automatically.
- **What it costs.** Nothing: it runs on your own computer using Google's free tier. The free tier limits
  how much can be indexed per day, which is why big documents are processed in the background.
- **Security.** Hashed passwords, lock-out after repeated failures, private libraries per account,
  protection against forged requests, a strict browser security policy, an audit log, and rate limits.
  It listens only on your own computer by default.

## If something goes wrong

| What you see | Why | What to do |
|---|---|---|
| "The AI service's usage limit was reached" | A Gemini free-plan limit (per minute or per day) | Wait a minute and ask again. If it persists, it's the daily limit: switch to the screen recording. |
| Answers take 30 to 90 seconds | Google's chat models are busy and the app is trying backups | Wait it out. For a presentation, put `CHAT_MODEL=gemini-3.1-flash-lite` in `.env` and restart: in testing the lighter model was the most reliable. |
| A document shows **Incomplete** | Indexing stopped partway (usually the daily limit) | Add the same file again once the limit resets; it continues where it stopped. |
| "Too many wrong passwords" | Five failed sign-ins locked the account for 15 minutes | Wait, or reset the password: `python -m app.users passwd NAME` |
| The launcher says something is missing | No `.venv` folder or no `.env` file | Follow the message; the README's Setup section covers both. |
| The launcher says the port is in use | Another program (or a second copy) is using port 8000 | Close it and try again. |
| The page can't reach the server | The launcher window was closed | Run the launcher again. |

## What it does not do (say so before someone asks)

- It reads text only: diagrams, images and scanned pages are invisible to it.
- It is built for one computer or a small trusted group, not for a company-wide rollout. There is no
  single sign-on, password-reset email or two-factor sign-in, and it needs HTTPS and hosting before it
  is used across a network (see "Accounts and security" in the README).
- Your document text is sent to Google's Gemini service to produce answers, so only use documents you
  are allowed to share with it.
