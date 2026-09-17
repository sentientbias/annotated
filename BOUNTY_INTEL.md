# Annotated $5K Bounty — Recon Intel (2026-09-16)

## Headline
- **Deadline:** NOT PUBLISHED anywhere accessible. No date/time on the bounty page, the /entries page, the TWiST bounties page, or the E2338 video description. (See confidence notes.)
- **Submission mechanism:** UNCONFIRMED. No submission form, link, or email is published. The bounty page says "submit your entry" with no channel named; only "Questions? Reach out via the LAUNCH team" with no contact info given.
- **Status:** Round 1 was already judged (E2338 scored a Top 3 from "dozens of amazing submissions"). The episode description says a ROUND TWO exists — "how to enter round two" is covered at 22:49 in the video, which I could not watch. The TWiST bounties page lists the Annotated bounty among "2 open" active bounties, 0 completed.

## Rules / judging criteria found
- Judging criterion (bounty page, echoed in a competitor's CLAUDE.md): "The cleanest, most complete execution wins."
- Code must be open source; Jason can cancel the bounty at any time (E2338 description).
- Prize: $5,000 cash, winner-takes-all. Jason owns the annotated.com domain; winner gets "potential ongoing work" (E2281 show notes).
- Hard requirements (from https://annotated.lovable.app/):
  1. Must ship as a Chrome **sidebar** extension (sidebar is the primary surface) — not just a web app.
  2. Visible **"File a claim"** button on every annotation page (fair-use dispute path).
  3. **Always link back to the original source URL** for all clipped content.
  4. Max clip length: **90 seconds**; video downgraded to **240p** (<480p).
  5. Auth via **X or Google OAuth only** (no email/password).
  6. Public social feed with follow + comment.
  7. Commentary supports text **and recorded audio**.
  8. Supported sources: YouTube videos, news articles, podcasts (text highlight / 90s audio clip).
- How-to-submit guidance (from https://thisweekinstartups.com/bounty): "Publish a working version of the product. Winners are called out on TWiST and earn the prize." No submission address given.

## Round 1 judged entries (from E2338 description, for reference)
- Submission 1: https://annotated.wtf — by Robert
- Submission 2: https://annotated.bytetalk.ai/feed — by Alan Shiflett (X: https://x.com/alanshiflett)
- Submission 3: https://anotated.com/ — by Chirag Asarpota (X: https://x.com/ChiragAsarpota)

## SOURCES CHECKED
1. **https://annotated.lovable.app/** (bounty brief) — ✅ fetched. Prize, concept, hard requirements, spec checklist, "submit your entry" with no channel. NO deadline, NO submission link/form/email. Only contact: "Questions? Reach out via the LAUNCH team" (no address). `browser.find` for "deadline" returned nothing.
2. **https://annotated.lovable.app/entries** (linked from a competitor's GitHub README as "Bounty entry" — not linked from the bounty page itself) — ✅ fetched, but the page is JS-rendered; text fetch returned only headers ("Contest Entries / The submissions. / Every build entered for the $5K annotated.com bounty."). No deadline or submission form visible in text.
3. **https://x.com/twistartups/status/2100269272899743841?s=46** — ❌ fetch failed (X requires login). Noted and moved on per task instructions.
4. **https://www.youtube.com/watch?v=X8SALDwOpTU** (E2338) — ✅ fetched description text (cannot watch video). Key findings: "so far, we've received dozens of amazing submissions"; Top 3 demos scored; rules = code is open source, Jason can cancel any time; links to all three submissions; "All active TWiST bounties" link (truncated in the description); a second open $5K bounty (podcast sidebar companion, Notion brief). NO deadline, NO textual submission instructions. Timestamp 22:49 "how to enter round two" exists only in the video — I cannot extract it without watching.
5. **https://thisweekinstartups.com/bounty** (TWiST bounties hub) — ✅ fetched; JS-rendered, bounty cards didn't come through in text. Shows "2 open" active bounties, 0 completed; generic flow: pick → build & ship → get featured. NO deadline, NO per-bounty submission mechanism.
6. **Web searches** (`browser.search`) — queries: "Calacanis $5000 bounty annotated chrome extension", "Jason Calacanis annotated bounty round two deadline entries", "annotated bounty TWiST deadline", "TWiST bounties thisweekinstartups.com annotated submit", "annotated.com bounty Calacanis submit entry open source round two". Turned up competitor GitHub repos (tmoody1973/annotated, kruschdev/annotated, txttlkvm/annotated — the last confirms entries live at annotated.lovable.app/entries) and an E2281 episode transcript noting the bounty announcement. NO deadline and NO submission channel found anywhere.

## Confidence
- Deadline unknown: HIGH confidence that it's not published on any accessible page (checked all four given sources + searches).
- Submission mechanism unconfirmed: MEDIUM confidence — there may be a submission form on the JS-rendered /entries page (or round-two instructions in the E2338 video audio) that text fetching couldn't reach. Recommends a live-browser look at https://annotated.lovable.app/entries and watching E2338 at ~22:49 ("how to enter round two").
- Rules/criteria: HIGH confidence (quoted verbatim from the bounty page and episode description).
