# SafeChat Guardian: Ethics notes

Monitoring a child's conversations is a serious intervention. This prototype tries to protect
children from online grooming while limiting the harms monitoring itself can cause. These notes
record the trade-offs and how the design responds.

## 1. False positives harm real people

* **Harm to the child:** a wrong alert can lead to a frightening confrontation, punishment, lost
  friendships, or a child who learns to hide their online life. All of these make the child *less*
  safe.
* **Harm to others:** a coach, teacher, older cousin or a kid of the same age can be wrongly
  suspected. Accusations can spread.
* **Evaluation:** we measure false-positive rates on **benign** and **hard-negative** conversations
  separately. Hard negatives are cases built to look suspicious but legitimate: coaches asking for
  ages on a registration form, kids comparing ages, classmates moving to the class Discord, team
  captains asking for usernames. In the first report the baseline had a low but non-zero false-positive
  rate at MEDIUM. Some misses came from new ways of phrasing ordinary parental check-ins. We report
  these rather than tuning on the test set.
* **Mitigations:**
  * **Hard floors** use high-precision signal combinations only (escalation together with secrecy or a
    platform move). A classifier score alone never triggers a floor.
  * A cooldown and level-rise-only alerting avoid a flood of alerts.
  * The alert wording describes *warning signs*, not guilt.
  * The guidance tells guardians to talk calmly, not to blame the child, and **not to confront the
    other person**.
  * A report is always a human decision, made with a checklist, never sent automatically.

## 2. False negatives

* Grooming that stops at friendship or risk assessment looks like normal chat. In evaluation,
  early-stopping trajectories are mostly **not** detected. This is intended: flagging every friendly
  adult would be worse. Guardians must not treat "no alert" as "safe".
* The notification-based capture misses muted chats, open chats, in-game chat without notifications,
  images, voice and video. The README and the app say so plainly.

## 3. Bias and representativeness

* The models are trained on English synthetic templates written by the developers. They encode the
  developers' assumptions about how grooming "sounds". Dialects, Taglish, slang, younger children's
  writing, LGBTQ+ teens' conversations, and neurodivergent communication styles may be scored
  differently. There is no basis yet to claim fairness across groups.
* Keyword rules can over-flag communities whose ordinary speech contains them ("send pics" of a game
  build, "meet at the mall" between classmates). We added `exclude` patterns and context-sensitive
  scoring, but these are partial fixes.
* **Before any real use:** evaluate on consented, ethically sourced, diverse data with child-safety
  experts. Report false-positive and false-negative rates by language and age group. Add Taglish with
  native speakers.

## 4. Over-surveillance of teens

* Adolescents need increasing privacy and autonomy. Constant monitoring can damage trust and their
  development, and can be misused for control (for example by an abusive household).
* Design choices:
  * **Visible, not covert.** There is a persistent notification and the icon is never hidden. The
    child sees an honest, age-appropriate notice. There is no stealth mode, and one will not be added.
  * **Summaries, not transcripts.** The guardian sees warning signs, not every message.
  * **Scoped.** Only allowlisted chat apps, with per-app toggles. Text only.
  * **Time-limited.** Data is deleted on acknowledgement on the phone, and on the server after the
    retention period (default 30 days).
* We recommend guardians adjust use to the child's age, for example by reviewing with an older teen
  whether to keep it on, and pair it with conversations about online safety rather than using it as a
  substitute for them.

## 5. Why alerts are summaries rather than transcripts

1. **Proportionality:** to act, a guardian needs to know *that* there is a risk and *what kind*, not
   every word.
2. **The child's dignity and privacy:** the child's side of the conversation (feelings, embarrassment,
   unrelated chats) stays private.
3. **Data minimization:** less stored text means less to leak, subpoena or misuse.
4. **Avoiding harmful content:** grooming conversations can contain sexual content. Transcripts would
   push that content into a parent's inbox and the system's database. Signals and short, redacted
   excerpts avoid this. The LLM is instructed never to reproduce such content.
5. **Better reactions:** a short, calm summary with guidance produces better conversations than a
   shocking transcript.

The guardian can still preserve evidence directly from the child's device (screenshots) if they
decide to report. That decision stays with the guardian, after the guardian has reviewed what
happened.

## 6. Dual use

The detection signals describe grooming *tactics*. To avoid giving abusers a playbook or a test bench:

* The synthetic data and rules are non-explicit and contain signals only.
* There is no feature that tells the other party they were detected, and no reply or engagement
  feature.
* The PAN-2012 loader never prints text.
