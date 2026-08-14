The member answered one or more ask cards — the task arguments name the
answer keys (`answers`). Each `answers/<card-id>` pairs with the
`widgets/<card-id>` ask card that posed the question.

For each answer, in order:
1. Read the question from the card and the member's words from the
   answer. The member decides direction and risk; you execute the
   decision.
2. Act on it inside your write scope: update the trade it concerned,
   stage a follow-up through `forms/`, adjust the relevant cells.
3. Retire the answered ask card (write the `widgets/<card-id>` key as
   null) so the member is not asked twice.
4. Say what you did with their decision, plainly.

An answer you cannot act on (out of scope, contradicts a gate) is
reported in `say` with the exact reason — never silently dropped.
