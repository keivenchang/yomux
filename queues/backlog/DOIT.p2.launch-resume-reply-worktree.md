# DOIT.p2.launch-resume-reply-worktree.md - Add Guarded Launch, Resume, Reply, And Cleanup

## Goal

Add launch dialogs behind `+ Claude`, `+ Codex`, and `+ Term`, recent-conversation resume, short peek/reply, optional worktree-backed launch, and dirty-worktree cleanup refusal.

## Plan

- [ ] Define one launch descriptor covering cwd, agent, model/profile, permission mode, initial prompt, optional session name, resume identity, and optional worktree/branch.
- [ ] Add a recent Claude/Codex resume picker scoped to selected cwd and one session peek/reply action shared with menus.
- [ ] Validate cwd, executable/profile, conversation identity, tmux/session uniqueness, branch/worktree ownership, authorization, and launch failure cleanup.
- [ ] Refuse worktree deletion when uncommitted changes exist; show the exact path and stop.

## Done Criteria

- [ ] Claude, Codex, and terminal launch/resume/reply paths consume one descriptor and have success, invalid, duplicate, cancellation, restart, and partial-failure tests.
- [ ] Dirty worktrees receive zero deletion commands; clean fixture worktrees retire only through exact authority.
- [ ] Focused backend/browser tests, the canonical gate, and restarted real launch/resume/reply journeys pass.
