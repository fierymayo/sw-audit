# SESSION-STATE — sw-audit
Updated: 2026-09-21 (after phase 3 build)

## Where things stand
- Phases 0–3 built and tested (51 tests green). Run folders (output/audit-<stamp>/) live.
- Phase 2 committed? verify `git log --oneline`; phase 3 NOT yet committed.
- Collections engine on real data: AUTOMATABLE=6, COLOUR_VERIFY=11, REVIEW=102, KEEP_MANUAL=23.

## Pending (in order)
1. Operator review of the 17 Ready-sheet rows (chat reviews suggested rules).
2. `python collections_audit.py --members --deliverable` (first members pull → real Adds).
3. Commit phase 3.
4. Loose end: check status of product "Mueller Pre Wrap Natural 2 Pack" (expected non-ACTIVE;
   if ACTIVE it's a subcat_sim bug).

## Division of labour
- claude.ai chat = design, specs, verified code, result interpretation.
- Cowork/Claude Code on this folder = run audits, read outputs, apply diffs, run tests.
- Never edit task-configs.json by hand; never write to the store. CLAUDE.md is the contract.