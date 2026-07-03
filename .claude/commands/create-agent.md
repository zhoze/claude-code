---
description: Interview the user and generate a new Claude Code agent from their answers
---

You are running the agent-creation interview. Your job is to gather requirements from the user with AskUserQuestion, then delegate generation of the agent file to the `agent-factory` subagent.

Initial description from the user (may be empty):

$ARGUMENTS

## Step 1: Interview

Ask the questions below with the AskUserQuestion tool. Batch them into at most two rounds (max 4 questions per call). Skip any question whose answer is already clear from the user's initial description — only ask what you genuinely don't know.

**Round 1:**

1. **Purpose** (skip if $ARGUMENTS describes it): "What should the new agent do?" Offer common archetypes as options — code reviewer, test generator, documentation writer, codebase analyzer — the user can always pick "Other" and type a custom purpose.
2. **Triggering style**: Should Claude launch this agent proactively whenever the task fits (e.g. automatically review code after changes), or only when the user explicitly asks for it?
3. **Tool access**: 
   - Read-only analysis (`Read, Grep, Glob`) — for reviewers/analyzers that must not modify anything
   - Read + write (`Read, Write, Edit, Grep, Glob`) — for generators and writers
   - Full access including Bash — for agents that run tests or commands
   - Custom — user specifies exact tools
4. **Model**: `inherit` (same as main conversation — recommended), `sonnet` (balanced), `haiku` (fast/cheap for simple tasks), or `opus` (most capable).

**Round 2:**

5. **Save location**: 
   - Project agent → `.claude/agents/` in the current repository (available only in this project, versioned with the repo)
   - Personal agent → `~/.claude/agents/` (available in all of the user's projects)
6. Optionally, one follow-up question if the answers so far leave something genuinely ambiguous (e.g. desired output format for a reviewer, scope limits for an analyzer). Skip this if nothing is ambiguous.

## Step 2: Delegate to agent-factory

Launch the `agent-factory` subagent (synchronously, not in the background) with a structured prompt containing everything collected:

```
Create a new agent definition with this specification:
- Purpose: [what the agent does, in the user's words plus your clarifications]
- Triggering: [proactive | on-demand only]
- Tools: [exact tool list, or "all tools"]
- Model: [inherit | sonnet | haiku | opus]
- Target directory: [.claude/agents/ | ~/.claude/agents/]
- Additional notes: [any free-text answers or "Other" responses from the interview]
```

## Step 3: Report

Relay the subagent's summary to the user:
- The agent's name and the file path that was created
- When the agent triggers and a suggested phrase to test it with
- Note that the file can be checked with `plugins/plugin-dev/skills/agent-development/scripts/validate-agent.sh <file>` (when working inside the claude-code repo)
- Remind the user that new agents are picked up at the start of a session, so they may need to restart or start a new conversation to use it
