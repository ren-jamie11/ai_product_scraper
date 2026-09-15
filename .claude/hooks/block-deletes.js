#!/usr/bin/env node
// PreToolUse hook: block file deletion/destruction in Bash & PowerShell commands.
//
// Claude Code's prefix permission rules ("Bash(rm *)") only match the first word
// of each command segment. That catches `rm x`, `a && rm x`, and `... | xargs rm`,
// but it cannot see a delete hidden inside an interpreter one-liner
// (python -c, node -e, cmd /c) or a .NET call. This hook receives the full
// command string on stdin and scans all of it.
//
// Exit 0 with no output = allow. Exit 0 with a deny payload = block.

let raw = "";
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {
  let cmd = "";
  try {
    const input = JSON.parse(raw);
    cmd = (input && input.tool_input && input.tool_input.command) || "";
  } catch (e) {
    process.exit(0); // unparseable input: stay out of the way
  }
  if (!cmd) process.exit(0);

  const hit = firstMatch(cmd);
  if (!hit) process.exit(0);

  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason:
          "Blocked by the block-deletes hook: this command can delete or destroy " +
          "files (matched: " + hit + "). Claude cannot bypass this. If the deletion " +
          "is intentional, run it yourself in a terminal.",
      },
    })
  );
  process.exit(0);
});

// High-confidence destructive patterns, matched anywhere in the command string.
const ANYWHERE = [
  [/\brm\s+(-[a-zA-Z]|["'$~.\/])/, "rm"],
  [/\brmdir\b/, "rmdir"],
  [/\bshred\b/, "shred"],
  [/\btruncate\s+-/, "truncate"],
  [/\bfind\b[^\n]*-delete\b/, "find -delete"],
  [/\bfind\b[^\n]*-exec\s+rm\b/, "find -exec rm"],
  [/\bxargs\b[^\n]*\brm\b/, "xargs rm"],
  [/\bdd\b[^\n]*\bof=/, "dd of="],

  // Destructive git operations
  [/\bgit\s+clean\b/, "git clean"],
  [/\bgit\s+rm\b/, "git rm"],
  [/\bgit\s+reset\s+--hard\b/, "git reset --hard"],
  [/\bgit\s+(checkout|restore)\s+(--\s+)?[.*]/, "git checkout/restore discarding changes"],

  // PowerShell cmdlets (unambiguous, multi-character names)
  [/\bRemove-Item\b/i, "Remove-Item"],
  [/\bRemove-ItemProperty\b/i, "Remove-ItemProperty"],
  [/\bClear-Content\b/i, "Clear-Content"],
  [/\bClear-Item\b/i, "Clear-Item"],
  [/\bFormat-Volume\b/i, "Format-Volume"],
  [/\bClear-Disk\b/i, "Clear-Disk"],

  // .NET reflection bypass
  [/\[\s*(System\.)?IO\.(File|Directory)\s*\]\s*::\s*Delete/i, "[IO.File]::Delete"],

  // cmd.exe shell-out
  [/\bcmd(\.exe)?\b[^\n]*\/c[^\n]*\b(del|rd|rmdir|erase)\b/i, "cmd /c del"],

  // Interpreter one-liners
  [/\bos\.(remove|unlink|rmdir|removedirs)\s*\(/, "python os.remove/unlink"],
  [/\bshutil\.rmtree\s*\(/, "python shutil.rmtree"],
  [/\.unlink\s*\(/, "Path.unlink()"],
  [/\bfs\.(unlink|rm|rmdir)(Sync)?\s*\(/, "node fs.unlink/rm"],
  [/\bunlink\s+["'$]/, "perl unlink"],
];

// Short aliases that are destructive only in command position (start of the
// command, or right after ; | && || or a newline). Scoping them this way keeps
// "del" inside a commit message or a filename from tripping the hook.
const ALIASES = /^(rm|ri|rd|del|erase|clc|rmdir|unlink|shred)\b/i;

function firstMatch(cmd) {
  for (let i = 0; i < ANYWHERE.length; i++) {
    if (ANYWHERE[i][0].test(cmd)) return ANYWHERE[i][1];
  }
  const segments = cmd.split(/\||;|&&|\n|\r/);
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i].trim().replace(/^[({\s]+/, "");
    const m = seg.match(ALIASES);
    if (m) return m[1].toLowerCase();
  }
  return null;
}
