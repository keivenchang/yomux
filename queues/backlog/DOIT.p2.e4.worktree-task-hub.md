# DOIT.p2.e4.worktree-task-hub.md - Add Worktree-Aware Parallel Agent Tasks

## Goal

Launch isolated worktree/branch/tmux tasks and show review, in-progress, blocked, and complete state across sessions without creating two writers in one tree.

## Plan

- [ ] Define task identity, repository/worktree/branch/session ownership, state transitions, review handoff, cancellation, failure, restart recovery, and cleanup authority.
- [ ] Add a guarded launch path and a task hub driven by durable server state rather than terminal-text guesses.
- [ ] Prove dirty-worktree refusal, duplicate-owner refusal, stale-task repair, provider-neutral status, and exact handoff artifacts.

## Done Criteria

- [ ] Every launched task owns one isolated worktree and session; no action may delete or reuse a dirty worktree.
- [ ] Browser, lifecycle, restart, and failure tests plus the canonical gate pass; a restarted live journey creates, observes, hands off, and safely retires one task.
