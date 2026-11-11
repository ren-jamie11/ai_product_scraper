---
name: refine-draft
description: Polishes and fleshes out rough drafts of notes, internal documentation, business writing, process descriptions, and product or tool descriptions while preserving the user's original meaning and voice. Use this skill whenever the user shares rough working material and asks to polish, refine, clean up, tighten, flesh out, rewrite, or "make this sound better", even if they don't name the skill. Also trigger when a draft contains embedded notes like "help me write this better" or "say this more clearly", or when the user wants feature ideas, improvement lists, or tool descriptions made clearer and more professional.
---

# Refine Draft

## Purpose

Use this workflow when the user provides a rough draft of notes, internal documentation, business writing, process descriptions, product/tool descriptions, or similar working material and wants it polished and fleshed out.

The goal is to preserve the user's original meaning and voice while making the draft clearer, more concise, more professional, and more useful. Do not rewrite for the sake of rewriting. If a section is already strong, keep it largely intact.

## Input

The user will provide a draft. It may contain:

- Rough notes or fragments
- Repetitive or awkward wording
- Parenthetical comments or reminders to themselves
- Incomplete explanations
- Sections that need stronger business rationale
- Examples that need to be expressed more clearly
- Informal wording that should remain natural rather than overly corporate

The user may also specify a preferred tone, audience, length, or purpose.

## Core Instructions

1. **Preserve the original intent.**
   - Do not change the underlying meaning, claims, priorities, or logic unless necessary for clarity.
   - Keep concrete details, examples, estimates, and constraints that matter.

2. **Do not over-edit.**
   - If wording is already clear and effective, leave it alone or make only minor improvements.
   - Prefer targeted edits over unnecessary rewrites.

3. **Improve clarity and flow.**
   - Remove repetition, awkward phrasing, filler, and unnecessary words.
   - Break up long or convoluted sentences.
   - Make the relationship between ideas explicit where needed.
   - Use clear transitions so each section flows naturally.

4. **Flesh out weak or incomplete ideas.**
   - When the user's intended point is clear but underdeveloped, articulate it more fully.
   - Explain not only **what** a feature, problem, or improvement is, but **why it matters** when that adds useful context.
   - Convert vague benefits into concrete business or user value when possible.

5. **Strengthen the value proposition.**
   - For tools, workflows, dashboards, or process notes, make the practical benefit easy to understand.
   - Connect functionality to outcomes such as saving time, improving decisions, increasing efficiency, reducing manual work, improving product development, or strengthening marketing.
   - Do not invent benefits that are not reasonably implied by the draft.

6. **Turn abstract ideas into concrete explanations.**
   - If the user gives an example in rough language, rewrite it so the reader immediately understands the use case.
   - When useful, explain how an insight translates into an action or decision.
   - Example pattern: an abstract review insight becomes a concrete visual reference that helps the user understand the specific design, color, material, or detail customers are reacting to.

7. **Maintain a natural professional tone.**
   - Clear, confident, concise, and practical.
   - Avoid inflated corporate language, buzzwords, or overly formal phrasing unless the user asks for it.
   - Prefer plain English.

8. **Improve structure where helpful.**
   - Use headings, short paragraphs, and bullets when they make the material easier to scan.
   - For improvement areas, a useful structure is:
     - **Short descriptive label:** what the improvement is.
     - Brief explanation of the current problem or limitation.
     - Proposed solution or desired functionality.
     - Why it matters / expected benefit.
     - Priority or estimated time savings if the user supplied them.

9. **Preserve uncertainty and priorities.**
   - Keep qualifiers such as "low priority," estimates, or temporary solutions when provided.
   - Do not make tentative ideas sound like finalized decisions.

10. **Fix embedded drafting notes.**
    - If the draft includes comments such as "help me write this better," "say this more clearly," or an unfinished thought in parentheses, infer the intended meaning from context and replace it with polished prose.

## Editing Standard

Before changing a sentence, ask:

- Is the original already clear?
- Can it be made shorter without losing meaning?
- Is the value or purpose obvious?
- Is there an unfinished idea that should be developed?
- Would a concrete example make the point easier to understand?

Only make changes that improve one or more of these dimensions.

## Output

Return a polished version of the user's draft that is ready to reuse.

Unless the user asks for commentary only:

- Keep the original structure when it works.
- Improve headings or bullet labels where useful.
- Preserve all important factual details.
- Flesh out incomplete sections directly in the revised draft.
- Do not add speculative claims or unnecessary content.

You may briefly mention the most meaningful improvements before or after the revised version, but the polished draft should be the primary output.

## Example Transformation

### Rough idea

> Have a product image display UI that lets the user clearly see the images of the product that a review or feature is referring to. E.g. the user finds that "bright playful colors" are very important. She wants to see what the reviewer actually meant.

### Refined version

> **Add a product image reference UI:** Allow users to easily view the product images associated with the features or feedback they are analyzing. For example, if the dashboard identifies **"bright, playful colors"** as an important feature, the user should be able to quickly see the product images that illustrate what customers are referring to. This helps translate abstract review insights into concrete visual references, making it easier to understand which specific designs, colors, materials, or product details are resonating with customers.

## Guiding Principle

**Polish what is weak, preserve what is strong, and add detail only when it makes the user's original idea clearer or more useful.**
