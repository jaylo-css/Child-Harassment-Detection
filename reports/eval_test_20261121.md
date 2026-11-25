# SafeChat Guardian evaluation

Generated 2026-11-21 01:17 on the synthetic **test** split: 89 conversations (45 benign, 13 hard negatives, 31 grooming). Thresholds: MEDIUM 40, HIGH 70, CRITICAL 85.

A conversation counts as flagged at a level if its score reached that level at any message (i.e. an alert would have fired). About 25% of templates are held out of training and appear only in the test split. The test set is small, so treat differences of a few points as noise.

## Summary

| engine | P@MED | R@MED | F1@MED | P@HIGH | R@HIGH | F1@HIGH | median first alert | latency F1 |
|---|---|---|---|---|---|---|---|---|
| baseline | 0.808 | 0.677 | 0.737 | 0.900 | 0.581 | 0.706 | 50% | 0.456 |
| llm | skipped | | | | | | | |
| ensemble | skipped | | | | | | | |

## Engine: baseline

Scored 89 conversations in 3.5 s.

| threshold | precision | recall | F1 | TP | FP | FN | FPR benign | FPR hard negatives |
|---|---|---|---|---|---|---|---|---|
| MEDIUM | 0.808 | 0.677 | 0.737 | 21 | 5 | 10 | 0.089 | 0.077 |
| HIGH | 0.900 | 0.581 | 0.706 | 18 | 2 | 13 | 0.044 | 0.000 |

**Early detection:** median first MEDIUM+ alert at **50%** of the conversation length (over 21 detected grooming conversations). Latency-weighted F1 (MEDIUM): **0.456**.

**Recall at MEDIUM by how far the grooming trajectory went** (early-stopping conversations are deliberately hard):

| true max stage | detected / total |
|---|---|
| FRIENDSHIP | 0 / 8 |
| RISK_ASSESSMENT | 0 / 2 |
| TRUST_EXCLUSIVITY | 2 / 2 |
| SECRECY | 4 / 4 |
| PLATFORM_MOVE | 8 / 8 |
| ESCALATION | 7 / 7 |

### Window-level stage confusion matrix

| gold \ pred | NONE | FRND | RISK | TRST | ISOL | SECR | PLAT | ESCL |
|---|---|---|---|---|---|---|---|---|
| **NONE** | 456 | 0 | 3 | 1 | 0 | 3 | 0 | 2 |
| **FRND** | 9 | 60 | 0 | 3 | 0 | 0 | 0 | 0 |
| **RISK** | 1 | 9 | 43 | 0 | 0 | 2 | 0 | 0 |
| **TRST** | 0 | 0 | 6 | 28 | 0 | 0 | 0 | 0 |
| **ISOL** | 0 | 0 | 6 | 2 | 17 | 0 | 0 | 0 |
| **SECR** | 0 | 0 | 2 | 2 | 4 | 32 | 1 | 3 |
| **PLAT** | 0 | 0 | 1 | 0 | 1 | 4 | 25 | 0 |
| **ESCL** | 0 | 0 | 0 | 0 | 0 | 1 | 4 | 13 |

Window accuracy (argmax vs. highest-order gold stage): 674/744 = 0.906

**Worst false positives** (highest-scoring non-grooming conversations)

| id | label | category | peak score | peak level | stages detected |
|---|---|---|---|---|---|
| syn-00064 | benign | parent_checkin | 93.7 | CRITICAL | TRUST_EXCLUSIVITY, SECRECY, ESCALATION |
| syn-00088 | benign | parent_checkin | 75.0 | HIGH | TRUST_EXCLUSIVITY, SECRECY |
| syn-00184 | benign | parent_checkin | 65.5 | MEDIUM | ESCALATION |
| syn-00250 | benign | parent_checkin | 62.2 | MEDIUM | RISK_ASSESSMENT, ESCALATION |
| syn-00360 | hard_negative | coach_teacher | 42.1 | MEDIUM | SECRECY |

**Worst false negatives** (lowest-scoring grooming conversations)

| id | true max stage | category | peak score | peak level | stages detected |
|---|---|---|---|---|---|
| syn-00412 | FRIENDSHIP | grooming_friendship_fast | 6.2 | LOW | FRIENDSHIP |
| syn-00402 | FRIENDSHIP | grooming_friendship_fast | 8.1 | LOW | - |
| syn-00410 | FRIENDSHIP | grooming_friendship_fast | 8.7 | LOW | FRIENDSHIP |
| syn-00409 | FRIENDSHIP | grooming_friendship_fast | 11.9 | LOW | FRIENDSHIP |
| syn-00404 | FRIENDSHIP | grooming_friendship_fast | 15.5 | LOW | FRIENDSHIP |

_PAN-2012 not present in data/pan2012/; skipped._

## Engine: llm

Skipped: no ANTHROPIC_API_KEY set (the app runs fully offline).

## Engine: ensemble

Skipped: no ANTHROPIC_API_KEY set (the app runs fully offline).
