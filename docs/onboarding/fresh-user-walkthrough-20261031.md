# Fresh-user onboarding: walkthrough + spec (2026-10-31)

Phone first, desktop later: the flow must be one shared implementation in taOS
(`desktop/src`), adapted per form factor, not two onboardings.

## 1. What Jay asked for (verbatim, 2026-10-31)

> configure taOS Agent on the phone to use hermes agent (make sure the hermes agent is also
> called taOS Agent and not default etc to save confusion as if an agent/harness change happens
> it keeps its identity anyway. Once installed I want to add a claude api key to secrets as a
> user would and then assign the gateway to taOS Agent. We should do this as a new user would
> actually, we could refine the onboarding process to be a refined apple like experience

> sign up, taOSgo registration optional, create an account and follow a prompted easy and quick
> guide to get chatting with your first agent using an on device model (maybe lfm3? research best
> on device model for quickish chat and basic tool calls). We should also have a set up taOS Agent
> now screen. the user should be advised we recommend a powerful/high quality model as it can
> change your system etc. we should skip for now but it should allow for using cloud providers and
> models etc available on your cluster (or free taOS models via our infrastructure in the future).
> This onboarding work will also be used for the desktop as they should be unified. we will target
> the phone first, adapt for desktop later

> we need a full (but skippable by sections) apple like onboarding process that leads to your
> first chat with an agent tailored to the users needs.

## 2. Walkthrough as measured on the glass

Handset: Nothing Phone (1), pmOS, taOS updated through Settings > Update to dev `e3efe6d1`,
then the data dir moved aside (backup kept on the device) so the real first run showed.

| Step | Screen | Shot |
|---|---|---|
| 1 | Single "Welcome, set up your account" card: username, full name, email (optional), password, PIN (optional), stay signed in | `shots-20261031/01-first-load.jpg`, `03-form-filled.jpg` |
| 2 | "Get started" lands directly on the home screen. Widgets show "0 agents". No next step. | `05-after-submit-9s.jpg` |
| 3 | Lock screen already lists a "taOS Agent, Idle" island | `06-bell.jpg` |
| 4 | Agents app: a "taOS agent" card labelled OPENCODE above an empty state "No agents deployed yet" | `09-agents-app.jpg` |

Measured from the API after step 4 (`GET /api/taos-agent/config`): `model: null`,
`framework: opencode`, `framework_preference: picoclaw`,
`framework_reason: "picoclaw preferred, picoclaw binary not found, using opencode"`,
`device_class: mobile`. No llama.cpp binary is installed on the handset.

## 3. Gaps found

1. **No guide after account creation.** The user lands on the home screen with nothing to do next.
2. **No path to a taOS cloud account.** Email is "optional today, used for cloud services later";
   nothing links to taos.my registration or taOSgo. Separately, taos.my returned
   `403 signups_closed` to a direct register call (with @taOS-website-dev).
3. **The system agent cannot chat.** `model: null` and no local runner: a new phone user has no
   working agent at all.
4. **The system agent's harness silently falls back.** Mobile prefers PicoClaw, the binary is
   absent, so it runs opencode. Hermes is not a supported system-agent framework
   (`FRAMEWORK_CHOICES` = auto, opencode, picoclaw, claude_code).
5. **Three inconsistent presentations of one agent.** Lock screen "taOS Agent / Idle", Agents app
   "taOS agent" (lowercase) with an OPENCODE badge, and "No agents deployed yet / 0 deployed"
   beneath it, plus "0 agents" on home.
6. **"New taOS build available" toast on a fresh install** right after a completed update; the
   toast's from-fingerprint differs from the served `version.json`. Needs one reload check.
7. **Linux user vs taOS user.** The form never says whether the username is also the system login.
8. **Waydroid session leftover split the screen** (taOSmobile, mine): a running Waydroid window
   tiled beside the kiosk. W2 must keep Android windows off the kiosk workspace by default.

## 4. Proposed flow (each section skippable, "Set up later" lands in the Setup checklist)

1. **Hello.** Full-bleed welcome, one button.
2. **Your account.** Name, username (states it is also the device login), password, optional PIN.
3. **taOS account (optional).** Sign in or create a taos.my account; taOSgo offered, not required.
   Skip keeps everything local.
4. **This device.** First device, or join an existing cluster (pair code / nearby).
5. **About you.** A few tap-to-pick chips (what you want help with, tone, how hands-on). Feeds the
   first agent's persona and suggested prompts.
6. **On-device model.** Recommended small chat model sized to the device, download with progress,
   runs offline. Skip allowed.
7. **Set up taOS Agent.** Explains taOS Agent can change the system, so a powerful, high quality
   model is recommended. Options: cloud provider key (stored in Secrets, gateway assigned to taOS
   Agent), a model from your cluster, free taOS models (later, shown as coming soon), or
   "Skip for now" (taOS Agent uses the on-device model with limited abilities).
8. **Privacy summary.** What stays on device, what leaves it.
9. **First chat.** Opens the chat with the personal assistant, three suggested prompts tailored
   from step 5.

Jay's ruling on the agent order (2026-10-31): the tailored first agent is a SEPARATE personal
assistant, created before taOS Agent ("maybe they should create their personal assistant first").
taOS Agent is its own onboarding step that explains what taOS Agent is for and that it is
powerful, which is why a powerful model is recommended; support for smaller on-device models for
taOS Agent is "coming soon".

Identity rule: the system agent is always named "taOS Agent" whatever harness or model sits
behind it; a harness swap must not rename it or reset its memory.

## 5. Build pieces

- **taOS core (tinyagentos):** `FRAMEWORK_HERMES` in the system-agent runtime with decision
  reasons (no binary, api_server not up), a Hermes runtime mirroring the PicoClaw one that streams
  NDJSON, the framework setting and UI listing it, and Hermes' own identity set to "taOS Agent".
  The `taos-hermes-base` image is already cached on the handset.
- **taOS desktop (shared):** the onboarding sections above, replacing the single account card;
  skipped sections feed `SetupChecklist`.
- **taOSmobile (device layer):** on-device runner install as a FILE (llama.cpp with LFM3 support),
  model fetch, Waydroid window placement.

## 6. On-device model (from @Strata-dev, Orange Pi measurements, not yet on this phone)

- Pick: **LFM3-1.7B**, Q4_0 for CPU (~1.05 GB) or Q8_0 (~1.85 GB) if decode speed holds.
  Held a 6-turn tool-calling chat: 6/6 answered, 5/5 cited.
- Runner-up: **Spark-X2.6-4B** Q4_K_M (~2.5 GB). Cleaner tool discipline, slower on A78.
- Runtime: llama.cpp CPU first (4 big cores), then bench Vulkan on turnip against it.
  Needs a llama.cpp build with LFM3 support.
- Known failure: on a forced final answer LFM3 re-emits earlier tool calls as text. Fix without a
  fork: on the retry, flatten context into one user message. The harness must do this.
- Sampling: temp 0.1, top_k 50, top_p 1.0, min_p 0, repeat 1.1; `enable_thinking: false` for speed.
  Keep onboarding turns short.

Jay (2026-10-31): test LFM3, and also look for larger quants or remixes that work better;
benchmark them on the phone.

## 7. Decided

- First agent: a separate personal assistant, created first; taOS Agent is its own later step.
- Personal taos.my account: operator-created by @taOS-website-dev (card tsk-cx3hj6, no signups flip);
  Jay sets the password from the reset email, then the account is comped.
