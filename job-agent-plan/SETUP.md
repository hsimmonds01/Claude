# Setup session — click by click

Everything below gets created **in her name, on her devices, logged into her
accounts.** You're there to read the steps out and troubleshoot, not to own
anything. If you find yourself typing your own password, stop — something has
gone wrong.

**Time:** 60–75 minutes. The original estimate of 45–60 was before the GitHub
token step was added, and that one is fiddly.

**What you need in front of you:** her phone, and a laptop. Most of this is
far easier on a laptop; only steps 3 and 7 need the phone.

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

## Step 2 — Create the repo · 3 min

The "repo" is the folder on GitHub that holds the whole thing.

1. Top right **+** icon → **New repository**.
2. **Repository name:** `job-agent` (or anything — no spaces).
3. **Description:** leave blank.
4. **Select Private.** ← This one matters. Her CV goes in here. Do not
   leave it on Public.
5. Tick **Add a README file**.
6. Click **Create repository**.

Write the address down in the scratch note — it'll look like
`github.com/hername/job-agent`. I need the exact username and repo name to
build against.

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
> don't fight it, tell me. There's a fallback (Resend for sending, and a
> different approach for reading) — it's just more accounts, so we try this
> way first.

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

It will fail on its first test run. That's expected — the workflow it's
trying to wake doesn't exist yet. We'll test it properly once there's code.

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

## Done — what to tell me

Send me these four things and I'll build against them:

1. The repo address — `github.com/username/job-agent`
2. Which steps worked and which didn't (especially **7c**, the app password)
3. Her **CV** (PDF is fine)
4. Her **target company list** — even five names is enough to start. Company
   name plus a link to their careers page. This feeds the best source in the
   system, so rough and quick beats perfect and later.

And she should fill in `profile.md` whenever suits — it's the one that makes
the scoring good, and it's easier to write after she's seen a first batch of
results than from a blank page.

---

## Checklist

Accounts made:

- [ ] GitHub, personal email, 2FA on, recovery codes saved
- [ ] Repo created, **Private**
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
- [ ] Scratch note deleted
