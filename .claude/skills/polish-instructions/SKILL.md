---
name: polish-instructions
description: Polishes and strengthens a draft AI prompt, system instruction, workflow instruction, agent instruction, or skill while preserving its original intent, structure, and strengths. Use this skill whenever the user shares a draft prompt or instruction set and asks to polish, refine, tighten, improve, clean up, flesh out, or review it, even if they don't name the skill. Also trigger for phrasing like "make this prompt better", "can you improve these instructions", or "polish this workflow".
---

# Polish Instructions

## Purpose

Use this workflow when I give you a draft AI prompt, system instruction, workflow instruction, agent instruction, or similar piece of guidance and ask you to polish it.

Your job is to improve the draft while preserving its original intent, structure, and strengths. Do not rewrite for the sake of rewriting. If the prompt is already strong, say so and make only changes that materially improve clarity, consistency, precision, robustness, or usefulness.

## Objective

Produce a stronger version of my draft that:

- preserves what is already working;
- makes the intended task and outcome clearer;
- removes ambiguity, redundancy, and awkward wording;
- adds missing constraints or decision rules when they would materially improve model behavior;
- makes important distinctions explicit;
- improves consistency and reliability across repeated use;
- remains practical rather than becoming unnecessarily long or complicated.

The goal is not simply better prose. The goal is a better-performing instruction.

## How to Review the Draft

Read the entire draft first and infer:

- the actual objective of the prompt;
- what output or behavior I ultimately care about;
- which instructions are essential versus incidental;
- where an AI model could reasonably misinterpret the instruction;
- where important edge cases, priorities, or boundaries are left implicit;
- whether the requested level of specificity is appropriate;
- whether examples, references, or source material are being used correctly;
- whether any wording encourages superficial pattern matching rather than the intended reasoning.

Evaluate the prompt as an instruction set, not just as writing.

## Editing Principles

### 1. Preserve strong material

Do not automatically change wording that is already clear and effective.

Keep useful terminology, examples, structure, and constraints unless there is a specific reason to improve them.

If the draft is already solid, make only targeted refinements.

### 2. Clarify the real objective

Make the downstream purpose explicit when doing so helps the model make better decisions.

For example, if a clustering prompt is ultimately intended to identify product features and pain points for quantitative analysis, state that purpose so the model optimizes for meaningful product concepts rather than surface-level word similarity.

### 3. Turn implicit expectations into explicit decision rules

Identify places where the desired behavior is understandable to a human but underspecified for an AI.

Where useful, clarify things such as:

- how to choose between two plausible interpretations;
- which consideration takes priority;
- what level of granularity is desired;
- whether categories should be mutually exclusive;
- how to handle overlap or ambiguity;
- what should happen when an input does not fit neatly;
- whether examples are rules, references, or merely illustrations.

Add these rules only when they materially improve reliability.

### 4. Distinguish semantic intent from surface wording

Where relevant, prevent the model from relying too heavily on shared words, syntax, or superficial similarity.

Encourage it to organize or reason according to the underlying concept, customer concern, task intent, or functional meaning.

### 5. Make examples function correctly

If the draft includes examples, clarify how they should be used.

Unless the draft clearly says otherwise, examples should generally teach the intended logic, level of specificity, and decision boundaries rather than force the model to copy the same labels or structure.

A useful formulation is:

> Treat the examples as the primary source of truth for the intended behavior, but do not mechanically copy their wording, labels, or structure when the new input calls for something different.

### 6. Tighten ambiguity without over-specifying

Add enough instruction to resolve meaningful uncertainty, but do not create dozens of unnecessary rules.

Prefer a small number of high-value principles over exhaustive micromanagement.

### 7. Improve terminology

Replace awkward, unnatural, vague, or technically imprecise wording when appropriate.

For example:

- prefer “semantic clustering” over “semantical clustering”;
- prefer concrete action verbs over vague phrases;
- use consistent terminology for the same concept throughout the prompt.

### 8. Preserve flexibility where judgment is useful

Do not make the prompt so rigid that it prevents reasonable model judgment.

Explicitly distinguish hard requirements from guiding principles where helpful.

## What to Add When Relevant

Depending on the task, consider whether the prompt would benefit from clearer instructions about:

- the primary goal;
- intended downstream use;
- source-of-truth hierarchy;
- output format;
- category or cluster exclusivity;
- granularity;
- edge cases;
- ambiguous inputs;
- compound inputs;
- duplicates or near-duplicates;
- positive versus negative concepts;
- exceptions;
- prioritization rules;
- consistency across large datasets;
- quality checks;
- when the model should ask clarifying questions;
- when it should infer reasonable rules instead of asking;
- what not to do.

Do not add these mechanically. Include only what improves the specific prompt.

## Response Style

When polishing a prompt for me:

1. Briefly assess the original draft.
2. Identify the most important improvements, especially those that affect model behavior rather than just wording.
3. Provide the complete polished prompt.
4. Briefly explain any particularly important addition or decision.
5. If the original prompt is already strong, explicitly say so and avoid unnecessary rewriting.

Keep the commentary concise. The polished prompt itself should be the main output.

## Preferred Tone

The rewritten instruction should usually be:

- clear;
- direct;
- structured;
- professional;
- practical;
- specific without being bloated;
- easy for another AI model to follow.

Avoid inflated language, unnecessary jargon, and excessive explanation inside the prompt.

## Important Constraint

Do not change the underlying task, business objective, or intended methodology unless the draft itself contains a clear contradiction or flaw.

When a potential change would meaningfully alter the methodology rather than simply improve the instruction, call it out instead of silently changing it.

## Default Standard

Use the following principle as the default:

**Preserve first, clarify second, strengthen where useful, and only expand when the expansion improves model behavior.**

The finished version should feel like a more precise and robust expression of what I was already trying to accomplish—not like a different prompt written from scratch.