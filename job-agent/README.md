# Your job agent

This looks for jobs for you four times a day, marks each one against your CV
and what you've said you want, and sends you the good ones. It runs on
GitHub's computers, not on your phone — your phone is just the remote control.

It's yours. Your account, your keys, your notifications. Nobody else can see
this repo and nobody else needs to be involved to change it.

---

## The four files you control

You never need to write code. Everything you'd want to change day to day lives
in one of these, and they're all plain English.

| File | What it's for |
|---|---|
| **`profile.md`** | What you want. Titles, salary floor, location, dealbreakers. |
| **`feedback.md`** | Corrections. "Too junior", "more like this one", "no agencies". |
| **`config.yml`** | The dials. How often it runs, how loud it is, when it's quiet. |
| **`cv.md`** | Your CV as text. Update it when your CV changes. |

**To edit any of them from your phone:** open the GitHub app → this repo → tap
the file → tap the pencil icon → make the change → **Commit changes**.

That's it. The next run picks it up — nothing needs rebuilding or restarting.

---

## Getting job alerts from Welcome to the Jungle, LinkedIn and Indeed

This is the clever part, and it's how the agent reaches sites that don't let
software look at their jobs directly.

You set up job alerts on any site you like. They email you as normal. Those
emails also reach a mailbox the agent can read, so it scores those jobs
alongside everything else.

**You don't have to re-sign-up anywhere, and you don't have to change the
email address on your existing alerts.** You set up one forwarding rule on
your current email, and everything carries on as it is. You keep receiving
your alerts exactly as before — forwarding sends a copy, it doesn't divert
anything away from you.

The rule only forwards emails from job sites. Nothing personal goes anywhere.

### If your email is iCloud (the Mail app on iPhone)

**Important: you can't do this in the Mail app on your phone.** iCloud's
forwarding rules only exist on the website. Easiest on a laptop, but it works
in a phone browser too.

1. Go to **icloud.com** in a browser and sign in.
2. Open **Mail**.
3. Click the **gear icon** (bottom left, or top of the sidebar) → **Settings**
   — on some versions it's called **Preferences**.
4. Go to the **Rules** tab.
5. Click **Add a Rule**.
6. Set the first dropdown to **is from** and type the sender address of one of
   your job alerts (see the tip below on finding it).
7. Set the action dropdown to **Forward to** and enter the agent's mailbox
   address.
8. Click **Done**.
9. **Repeat steps 5–8 for each job site you get alerts from** — one rule per
   sender.

**Tip — finding the exact sender address:** open one of your job alert emails
in Mail, tap the sender's name at the top, and it shows the real address it
came from. It's usually something like `jobalerts-noreply@linkedin.com`, not
just `linkedin.com`. Use that full address.

**Why one rule per site:** iCloud's rules match a specific sender rather than
a whole website, so LinkedIn, Indeed and Welcome to the Jungle each need their
own rule. Slightly tedious once, then never again.

**Heads up:** the rule only applies to emails that arrive *after* you create
it. Alerts already sitting in your inbox won't be picked up — that's fine, new
ones start flowing straight away.

If iCloud gives you any trouble here, tell whoever set this up with you — the
fallback is changing the delivery address on the alerts themselves, which is
more clicking but always works.

### Adding a new job site later

This is the bit worth remembering. **Any** job site, ever, forever:

1. Make a job alert on that site, sent to your normal email as usual.
2. Add one more forwarding rule for its sender address.

Done. The agent picks it up on the next run. No code, nobody to ask.

---

## What you'll get

| What | When |
|---|---|
| **Phone alert** | Only for really strong matches. Up to 2 a day. |
| **Email digest** | Twice a day, everything else, best first. |

**Phone alerts are deliberately vague.** Your lock screen will say something
like "2 new matches" — never a company name or a job title. You only see the
detail after you unlock and tap. That's on purpose, since you're still in your
current job. You can turn it off in `config.yml` if you ever stop needing it.

---

## Steering it

Every alert has a link straight to `feedback.md`. Tap it, add a line, save.
Takes about fifteen seconds and the next run is sharper.

You don't have to be polite or precise. Real examples that work:

- `too junior`
- `no more agencies`
- `more like this one: <paste the link>`
- `stop showing me anything at <company>`
- `this is exactly right`

There are two sections in that file:

- **Standing rules** — permanent things. Edit and delete these freely.
- **Recent reactions** — quick notes. Add to the bottom; old ones fade out on
  their own, so you can contradict yourself later and the newest wins.

---

## Turning it down, or off

Open `config.yml`:

- **Too many phone alerts?** Raise `push_threshold` from 8 to 9.
- **Not enough?** Lower it to 7.
- **Too much email?** Lower `max_roles_per_digest`, or set
  `send_on_run_hours` to just one time.
- **Need a break?** Set `enabled: false` at the very top. It keeps collecting
  quietly and sends you nothing. Set it back to `true` and you get the backlog.
  Nothing is lost.

---

## If something looks wrong

**No emails at all?** The agent didn't run. Check the **Actions** tab in this
repo — it lists every run. If the last one is days old, the timer at
cron-job.org has stopped poking it.

**An email saying a run failed?** That's working as intended — it's designed
to tell you when it breaks rather than going quiet. Open a Claude session and
paste it what the Actions tab says.

**Getting rubbish jobs?** That's `feedback.md`, not a bug. Add a line saying
what's wrong with them.

**Silence should only ever mean "nothing matched" — never "it's been dead for
a fortnight".** That's why the failure email exists.

---

## Cost

£0 a month. No payment card is needed anywhere, and none has been entered.
Everything used here is on a free tier that stays free at this volume.
