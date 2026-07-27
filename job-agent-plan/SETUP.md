# Setup session — click by click

> **When this happens: AFTER the agent is built, not before.** Per PLAN.md §8,
> the code is built first in `job-agent/` on a branch of `hsimmonds01/Claude`.
> This session is the go-live — at the end of it, that finished, working agent
> is copied into her brand-new empty repo as its first commit and starts
> running. Her repo ends up with no link back to mine: no fork, no shared
> history, nothing to unpick.
>
> **Her CV and her filled-in `profile.md` are created for the first time
> during this session, inside her repo.** They are never committed to the
> public build repo. Everything there is a blank template.
>
> That means you arrive with something she can poke at and react to, rather
> than an empty checklist. It also means **Step 2 matters more than it looks**
> — the repo has to be created completely empty or the built agent won't push
> into it.

Everything below gets created **in her name, on her devices, logged into her
accounts.** You're there to read the steps out and troubleshoot, not to own
anything. If you find yourself typing your own password, stop — something has
gone wrong.

**Time:** 60–75 minutes. The original estimate of 45–60 was before the GitHub
token step was added, and that one is fiddly.

**What you need in front of you:** her phone, and a laptop. Most of this is
far easier on a laptop; only steps 3 and 7 need the phone.

**Needed before the session even starts** (ask her for these in advance —
they're a conversation, not an account):

- Her **CV**
- Her **target company list**, even roughly
- Her **`profile.md` answers** — or at least a chat through them

Without those the build has nothing real to score against, and the session
turns back into an empty checklist.

---

## Before you start

**Read this bit out to her first.** It sets expectations and takes a minute.

- Nothing will ever run on her phone. It runs on GitHub's computers, on a
  timer. Her phone is just the remote control.
- Everything here is free. No card is entered at any point in this guide.
  If any site asks for payment details, stop and check with me first — it
  means something has changed and there's a free route we've missed.
- She'll be making about eight accounts. That's the boring part and it's
  front-loaded. After today she never touches most of them again.
- Some of these sites will have redesigned since this was written. If a
  button isn't called what I say it's called, **don't guess** — note what
  you actually see on screen and we'll sort it in the next session. Nothing
  here is urgent enough to be worth a wrong click.

**Set up a scratch note** on the laptop — Notes, a text file, anything — to
paste keys into as you collect them. There's a checklist at the bottom of
this file of everything that should end up in it. **Delete that note at the
end of the session**, once everything's pasted into GitHub.

---

## Step 1 — Her GitHub account · 5 min

This is the account that owns everything. It must be a **personal email
address, not her work one.**

1. Laptop browser → **github.com**
2. Click **Sign up** (top right).
3. Enter her personal email → **Continue**.
4. Create a password → **Continue**.
5. Choose a username → **Continue**. Anything she likes; it's not public
   anywhere she cares about, and the repo will be private.
6. Answer the "email preferences" question (**n** is fine) → **Continue**.
7. Solve the puzzle → **Create account**.
8. She'll get a code by email. Type it in.
9. When asked "How many team members?" and similar — click **Skip
   personalization** if it's offered, or pick anything. Doesn't matter.
10. When it offers Free vs Pro plans → **Continue for free**.

**Then turn on two-factor authentication.** GitHub will nag until it's done
and her CV is going to live in this account.

11. Top right avatar → **Settings** → **Password and authentication** in the
    left sidebar.
12. **Enable two-factor authentication** → follow it through with an
    authenticator app on her phone.
13. **Save the recovery codes it gives you.** Screenshot them into her photos
    at minimum. Losing these locks her out permanently.

---

## Step 2 — Create the repo, completely empty · 3 min

The "repo" is the folder on GitHub that holds the whole thing. The built
agent gets pushed into it later in this session.

1. Top right **+** icon → **New repository**.
2. **Repository name:** `job-agent` (or anything — no spaces).
3. **Description:** leave blank.
4. **Select Private.** ← This one matters. Her CV goes in here. Do not
   leave it on Public.
5. **Leave "Add a README file" UNTICKED.** Leave the .gitignore and licence
   dropdowns on **None**. The repo must be created with nothing in it at all.
6. Click **Create repository**.
7. You'll land on a page of setup instructions rather than a file list. That's
   the right screen — it means the repo is genuinely empty.

> **Why this matters:** the agent is already built, with its own history, in a
> folder on the laptop. Pushing it in only works if GitHub's side is empty. If
> GitHub has even one commit of its own (which ticking "Add a README" creates),
> the push gets rejected as "updates were rejected" and it turns into a
> merge job in the middle of the session. Easy to avoid, annoying to undo.

Write the address down in the scratch note — it'll look like
`github.com/hername/job-agent`.

---

## Step 3 — Claude Pro on her phone · 3 min

This is the only thing in the guide that costs money (~£16/month), and it's
optional-but-planned — it's what lets her change how the agent *works*
rather than just what it looks for.

1. Phone → App Store / Play Store → install **Claude**.
2. Open it → sign in with her personal email.
3. Profile → **Upgrade** → **Pro**.

Also install the **GitHub** app on her phone while you're there. That's how
she'll edit the settings files day to day.

---

## Step 4 — Adzuna key · 5 min

Adzuna is a free UK jobs API. This is one of the two wide-net sources.

1. Laptop → **developer.adzuna.com**
2. Click **Sign up** / **Register** (top right).
3. Fill in name, email, and where it asks what you're building, something
   plain and true: *"A personal tool to search for jobs for myself."*
4. Confirm the email it sends.
5. Sign in. You'll land on a dashboard showing two values:
   - **Application ID** (short, digits)
   - **Application Key** (long, letters and digits)
6. Paste **both** into the scratch note, clearly labelled. They're different
   things and both are needed.

---

## Step 5 — Reed key · 5 min

Reed is the second wide-net UK source.

1. Laptop → **reed.co.uk/developers**
2. Look for **Sign up for an API key** / **Get started**.
3. Register with her email, confirm it.
4. Sign in → the key is shown on the account/API page. One long string.
5. Paste into the scratch note.

> If Reed's signup is broken or wants a business account, skip it. Adzuna
> plus the company careers pages plus the alert inbox is plenty. Tell me and
> I'll leave Reed switched off in `config.yml`.

---

## Step 6 — Gemini key (the AI that reads the jobs) · 5 min

1. Laptop → **aistudio.google.com**
2. Sign in with the **dedicated Gmail from Step 7 if you've done it already**
   — otherwise her personal Google account is fine.
3. Click **Get API key** (left sidebar, or top right).
4. Click **Create API key**.
5. If it asks which Google Cloud project → **Create API key in new project**.
6. Copy the key (starts `AIza...`) into the scratch note.

**Do not add billing.** It will work fine without it. If anything on that
page pushes you toward "enable billing", ignore it — we're deliberately not
using the feature that requires it.

---

## Step 7 — Dedicated Gmail + app password · 15 min

This is the most important step in the guide and the most likely to snag.
It does **two** jobs: the agent reads her job alerts from this inbox, and
sends her digests from it.

**Do this step early.** If Google has changed the rules on app passwords, we
need to know now, not at the end.

### 7a — The account

1. Laptop → **gmail.com** → **Create account** → **For myself**.
2. Pick an address. Something forgettable and not obviously hers — this
   address goes on job sites. `hj.alerts.2026@gmail.com`, not
   `hannah.jones.jobsearch@gmail.com`.
3. Work through it. It may ask for a phone number to verify — that's fine.

### 7b — Two-Step Verification (required for the next bit)

4. **myaccount.google.com** → **Security** (left sidebar).
5. **How you sign in to Google** → **2-Step Verification** → **Get started**.
6. Follow it through with her phone number.

### 7c — The app password

An "app password" is a one-off password that lets a program read and send
mail on her behalf, without ever knowing her real password. It can be
revoked on its own at any time.

7. Go to **myaccount.google.com/apppasswords** (type it directly — it's hard
   to find in the menus).
8. **App name:** type `job agent` → **Create**.
9. It shows a **16-character password in four blocks of four.** Copy it into
   the scratch note. **You cannot see it again after closing this box.**
   Spaces don't matter, they get stripped.

> **If that page says the option isn't available for your account:** stop,
> don't fight it. It's not broken and it's not fatal — go to
> **Appendix A** at the bottom of this file, which has three fallbacks. We try
> this route first only because it's the one that needs no extra accounts.

---

## Step 8 — Her job alerts, pointed at that inbox · 10 min

This is what makes the whole thing work for sites that have no API — the
agent reads her alert emails and treats them as a job source. Welcome to the
Jungle, LinkedIn and Indeed all get in through this door.

**She should drive this bit** — they're her searches.

On each site she cares about:

1. Sign in (or make an account) using the **new Gmail from Step 7**.
2. Run a job search the way she normally would.
3. Find **Create alert** / **Save search** / **Get notified** — the wording
   differs per site, it's usually near the search results header or a bell
   icon.
4. Set frequency to **Daily** if there's a choice.
5. Confirm the alert — most sites send a "confirm your alert" email to that
   Gmail that must be clicked.

Start with **Welcome to the Jungle** — the plan notes she particularly likes
it, and it's the main reason this route exists. Then LinkedIn, Indeed, and
anything niche in her field.

**Tell her the good bit:** any time in future she finds a new job site, she
just makes an alert there pointed at this address and the agent picks it up.
No code, no asking anyone.

**LinkedIn aside, unrelated to the build but worth her knowing:** job alerts
are private, but the **"Open to Work" badge is public** and her current
employer can see it. Different setting. Worth checking which one she has on.

---

## Step 9 — ntfy (the phone buzz) · 5 min

ntfy is a free app that lets the agent send a notification to her phone.
No account needed.

1. Phone → App Store / Play Store → install **ntfy**.
2. Open it → **+** to add a subscription.
3. **Topic name** — this is the important bit. It must be **long and
   random**, because the topic name is the only thing stopping a stranger
   reading her notifications. There's no password on top of it.

   Good: `hj-9f4k2xq7m3`
   Bad: `hannah-jobs`

   Make one up now, mixing letters and digits, at least 12 characters.
4. Leave the server as the default (`ntfy.sh`) → **Subscribe**.
5. Paste the topic name into the scratch note **exactly**, including dashes.

**Treat the topic name like a password.** Don't put it in a screenshot, don't
send it over work chat.

---

## Step 10 — GitHub access token (for the timer) · 10 min

The hardest step. This is a key that lets the timer site wake the agent up.

Do it slowly — **getting the permission wrong means starting the whole step
again.** Editing a token's permissions afterwards does *not* change a key
you've already copied. (Learned that the hard way on the bike-dock project.)

1. Laptop, signed in as her → top right avatar → **Settings**.
2. Scroll to the **bottom** of the left sidebar → **Developer settings**.
3. Left sidebar → **Personal access tokens** → **Fine-grained tokens**.
4. Click **Generate new token**.
5. **Token name:** `cron trigger`
6. **Expiration:** pick the longest available (usually 1 year). Put a
   reminder in her calendar for a month before it expires — when it does,
   the agent silently stops being woken up.
7. **Repository access:** select **Only select repositories** → in the
   dropdown, choose **job-agent**. Not "All repositories".
8. **Permissions** → expand **Repository permissions**.
9. Scroll down the long list to **Actions**. Change its dropdown from
   *No access* to **Read and write**.
10. Leave every other permission alone.
11. Scroll to the bottom → **Generate token**.
12. Copy the token (starts `github_pat_...`) into the scratch note.
    **It's shown once.**

---

## Step 11 — cron-job.org (the alarm clock) · 10 min

GitHub has its own timer but it's unreliable — it's run over an hour late on
this repo's other projects, which for a job alert means missing the morning
entirely. cron-job.org is the dependable one.

1. Laptop → **cron-job.org** → **Sign up** → confirm the email.
2. **Set the timezone first**, before making the job: account menu →
   **Settings** → set timezone to **Europe/London**. This means it handles
   the clocks going forward and back by itself — no twice-yearly fiddling.
3. **Create cronjob** (or the **+** button).
4. **Title:** `job agent`
5. **URL:** this exactly, with her username and repo name filled in —

   ```
   https://api.github.com/repos/HERUSERNAME/job-agent/actions/workflows/job-agent.yml/dispatches
   ```

6. **Schedule:** choose the custom / expert option, then tick the individual
   hours **7, 12, 16, 18** and set minutes to **0**. One job covers all four
   — you don't need four separate jobs.
7. Find the **Advanced** section:
   - **Request method:** change from GET to **POST**
   - **Request body:** `{"ref":"main"}`
   - **Headers** — add three, each a name and a value:

     | Name | Value |
     |---|---|
     | `Accept` | `application/vnd.github+json` |
     | `Authorization` | `Bearer github_pat_...` (the Step 10 token) |
     | `X-GitHub-Api-Version` | `2022-11-28` |

   The word `Bearer` and one space go **before** the token.
8. **Create** / **Save**.

Don't press its "test run" button yet — the code isn't pushed until Step 13.
If you do, it'll return a 404 and that's meaningless at this point rather
than a real failure. Step 13 tests it properly.

---

## Step 12 — Paste everything into GitHub · 10 min

Last step. This is where the keys go so the agent can use them, and it's the
only place they should live long-term.

1. Laptop → her repo → **Settings** tab (along the top of the repo, not the
   account settings).
2. Left sidebar → **Secrets and variables** → **Actions**.
3. For each row in the table below: click **New repository secret**, type the
   **Name** exactly as written (capitals, underscores), paste the value into
   **Secret**, click **Add secret**.

| Name | Value | From |
|---|---|---|
| `ADZUNA_APP_ID` | Application ID | Step 4 |
| `ADZUNA_APP_KEY` | Application Key | Step 4 |
| `REED_API_KEY` | Reed key | Step 5 (skip if skipped) |
| `GEMINI_API_KEY` | starts `AIza...` | Step 6 |
| `GMAIL_ADDRESS` | the new @gmail.com address | Step 7 |
| `GMAIL_APP_PASSWORD` | the 16 characters | Step 7 |
| `NTFY_TOPIC` | the random topic name | Step 9 |
| `DIGEST_TO` | **her normal personal email**, not the alerts Gmail | — |

Names must match exactly — the agent looks them up by name and a typo shows
up as a confusing failure later rather than an obvious one now.

Once these are in, **delete the scratch note.** GitHub hides secrets from
everyone including her from this point, which is correct — if one is ever
needed again it gets regenerated at source, not looked up.

---

## Step 13 — Push the agent in and watch it run · 10 min

This is the moment it becomes hers. My part; she watches.

1. On the laptop, in the built folder, point it at her empty repo and push.
   The whole build lands as her repo's first commit — nothing of mine
   attached to it, no transfer, no key rotation.
2. Refresh her repo page. Files appear.
3. **Actions** tab → she should see the workflow listed.
4. Go back to cron-job.org and hit **Test run** on the job now. Within a few
   seconds the Actions tab should show a run starting.
5. Watch it through. First real run:
   - a test push notification should land on her phone
   - a digest should arrive at her personal email
6. **Have her send one thing from her own phone before you leave:** open the
   GitHub app, find `feedback.md`, add a line, commit it. That's the loop
   that makes the whole thing work, and it's worth her having done it once
   with someone next to her rather than discovering it alone in a fortnight.

If the digest lands in spam, mark it **not spam** once — it's coming from her
own Gmail so it shouldn't, but worth checking on the first one.

---

## After the session — what to send me

1. Which steps worked and which didn't, especially **7c** (the app password)
   and **10** (the token permission).
2. Anything where the button wasn't called what I said it was called.
3. Her first reaction to the scoring after a day or two — "too generous",
   "missing obvious ones", "keeps showing agencies". That's the tuning pass,
   and it's much better information after real results than before.

`profile.md` is easier to fill in once she's seen a batch of real results
than from a blank page, so don't push for it to be perfect on day one.

---

## Checklist

Have before starting:

- [ ] Her CV
- [ ] Target company list
- [ ] `profile.md` answers, or a conversation through them
- [ ] The agent built and working locally

Accounts made:

- [ ] GitHub, personal email, 2FA on, recovery codes saved
- [ ] Repo created, **Private**, and **completely empty** (no README)
- [ ] Claude Pro on her phone
- [ ] GitHub app on her phone
- [ ] Adzuna
- [ ] Reed *(optional)*
- [ ] Google AI Studio
- [ ] Dedicated Gmail, 2-Step Verification on
- [ ] cron-job.org, timezone Europe/London
- [ ] ntfy app, random topic subscribed

Collected and pasted into repo secrets:

- [ ] `ADZUNA_APP_ID`
- [ ] `ADZUNA_APP_KEY`
- [ ] `REED_API_KEY`
- [ ] `GEMINI_API_KEY`
- [ ] `GMAIL_ADDRESS`
- [ ] `GMAIL_APP_PASSWORD`
- [ ] `NTFY_TOPIC`
- [ ] `DIGEST_TO`

Also done:

- [ ] Job alerts created on her chosen sites, pointed at the new Gmail
- [ ] Alert confirmation emails clicked
- [ ] GitHub token made, Actions = Read and write, repo-scoped
- [ ] cron-job.org job created with POST, body and three headers
- [ ] Token expiry date in her calendar
- [ ] Build pushed to her repo, Actions tab shows the workflow
- [ ] cron-job.org test run fired a real GitHub Actions run
- [ ] Test push notification landed on her phone
- [ ] Test digest landed in her personal email
- [ ] **She** edited `feedback.md` from her phone once, herself
- [ ] Scratch note deleted

---

## Appendix A — if the Gmail app password doesn't work

The app password does **two separate jobs**. Worth knowing which one has
failed, because they have different fallbacks and it's rare for both to be
blocked:

| Job | What it does | Protocol |
|---|---|---|
| **Reading** | Pulls her job-alert emails out of the inbox so they can be scored | IMAP |
| **Sending** | Sends her the twice-daily digest | SMTP |

Neither is load-bearing for the *core* agent. Adzuna, Reed, company feeds,
scoring and push notifications all work with no email at all. Worst case she
gets phone alerts and no digest, which is a degraded but genuinely usable
product. Don't let this block go-live.

### Option 1 — Google App Password, in a different place *(try first, 2 min)*

Most "app passwords unavailable" reports are one of three fixable things, not
a policy block:

- **2-Step Verification isn't fully on.** It must be *confirmed*, not just
  started. Go back to step 7b and check it says "On".
- **The account is under 24 hours old.** Google sometimes withholds app
  passwords on brand-new accounts. Waiting a day fixes it.
- **Advanced Protection is on**, or it's a school/work Google account.
  Then app passwords are genuinely blocked and no amount of clicking helps.

If it's the age one, the pragmatic move is: make the Gmail account a day or
two *before* the setup session. Worth doing as standard.

### Option 2 — Resend for sending, keep Gmail for reading *(10 min)*

If sending is the problem, this is the proven route — it's exactly what the
Daily Discovery project in this repo already uses.

1. Laptop → **resend.com** → **Sign up** (free tier, no card).
2. Confirm the email, sign in.
3. Left sidebar → **API Keys** → **Create API Key**.
4. Name it `job agent`, permission **Sending access**, → **Add**.
5. Copy the key — it starts `re_` — into the scratch note.
6. Add it to the repo secrets as **`RESEND_API_KEY`**.

Two things to expect: it sends from `onboarding@resend.dev` until a domain is
verified, so **the first digest may land in spam — mark it "not spam" once**
and it behaves after. And the free tier is capped per day, which at two
digests a day is not remotely a concern.

### Option 3 — an email provider that still does app passwords *(15 min)*

If *reading* is the problem — the important half, since it's the route to
WTTJ, LinkedIn and Indeed — the answer is a different mailbox provider, not a
different technique. She'd make the alerts mailbox somewhere that still issues
per-app passwords for IMAP. Fastmail (paid, ~£3/mo) and Proton Mail with its
Bridge both do; several free providers do too, though I'd want to check which
are current at the time rather than name one from memory here.

The job alerts get pointed at that address instead. Nothing else changes —
the agent reads IMAP the same way regardless of who runs the mailbox. Only
worth doing if Option 1 has genuinely failed, since it's a paid step for
something Gmail does for free.

### Option 4 — ship without email ingestion, add it later

Perfectly reasonable. Go live on Adzuna + Reed + company feeds + push
notifications, and treat the alert inbox as a follow-up once the mailbox
question is settled. She gets a working agent on the day either way, and this
is a one-file change to add afterwards — no rebuild, no re-setup.

**What I'd actually do:** create the Gmail a couple of days early, so if
Option 1's 24-hour rule is the cause we never even meet the problem.
