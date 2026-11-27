// PreToolUse guard for Bash and PowerShell: blocks git push and package installs into the conda
// environment, in any segment of a chained command or a nested shell's script.
// Exit code 2 blocks the call and shows stderr to Claude.
import { readFileSync } from "node:fs";

const input = JSON.parse(readFileSync(0, "utf-8"));
const command = String(input.tool_input?.command ?? "");

function block(reason) {
  process.stderr.write(`Blocked by .claude/hooks/guard-git.mjs: ${reason}\n`);
  process.exit(2);
}

const PUSH = "git push is forbidden (AGENTS.md Sessions and Git).";
const DEPENDENCY =
  "installing or removing a package needs the owner's approval (AGENTS.md Rules, Installing software)." +
  " Ask the owner, stating the package, why it's needed, its size and maintenance status, and the alternatives; they run the command.";

// Quoted tokens stay whole, so a commit message mentioning "push" doesn't trigger.
const unquote = (token) => token.replace(/^(["'])([\s\S]*)\1$/, "$2");
const tokenize = (text) => text.match(/"[^"]*"|'[^']*'|\S+/g) ?? [];

function isPush(globals) {
  let i = 0;
  while (i < globals.length && globals[i].startsWith("-")) {
    i += /^-[Cc]$/.test(globals[i]) ? 2 : 1; // global options; -C and -c take a value
  }
  return globals[i] === "push";
}

// A segment's program name and its unquoted arguments.
function parse(segment) {
  const raw = tokenize(segment);
  // Skip the PowerShell call operator, `env` and `VAR=value` prefixes.
  let start = 0;
  while (raw[start] === "&" || raw[start] === "env" || /^\w+=/.test(raw[start] ?? "")) start++;
  // Unquoted, so `& "C:\Program Files\Git\bin\git.exe" push` counts.
  const program = unquote(raw[start] ?? "").toLowerCase().replace(/^.*[\\/]|\.exe$/g, "");
  return [program, raw.slice(start + 1).map(unquote)];
}

const firstPositional = (args) => args.find((a) => !a.startsWith("-"));

function check(text) {
  // Heredoc and here-string bodies are data, so a line reading `git push` in one is not a command,
  // unless a shell named before the operator on that line runs the body (`bash <<EOF`).
  const shell = text
    .replace(/(?<!(?<![\w.-])(?:bash|sh|pwsh|powershell)\b[^\n]*)<<-?\s*(["']?)([A-Za-z_]\w*)\1([^\n]*)\n[\s\S]*?\n[ \t]*\2[ \t]*(?=\n|$)/g, "<<$2$3")
    .replace(/(?<!(?<![\w.-])(?:bash|sh|pwsh|powershell)\b[^\n]*)@(["'])\r?\n[\s\S]*?\r?\n\1@/g, "''");

  // Quote-aware split, so a `;` or `|` inside a quoted string doesn't start a segment.
  const segments = [""];
  for (const piece of shell.match(/"[^"]*"|'[^']*'|&&|\|\||[;|\n(){}]|[^"';|\n(){}&]+|["'&]/g) ?? []) {
    if (/^(&&|\|\||[;|\n(){}])$/.test(piece)) segments.push("");
    else segments[segments.length - 1] += piece;
  }

  for (const segment of segments) {
    const [program, args] = parse(segment);
    if (program === "git" && isPush(args)) block(PUSH);
    // Permission prompts don't appear in auto mode, so package changes are refused here.
    const positionals = args.filter((a) => !a.startsWith("-"));
    // `mamba install …`, `conda env update …`
    const condaCommand = positionals[0] === "env" ? positionals[1] : positionals[0];
    if (["mamba", "micromamba", "conda"].includes(program) && ["install", "remove", "uninstall", "update", "create"].includes(condaCommand)) block(DEPENDENCY);
    // `pip install …`, `uv pip install …`, `python -m pip install …`, `py -m pip install …`
    const pip = args.indexOf("pip");
    const viaPip =
      /^pip[\d.]*$/.test(program) ? args
      : program === "uv" && pip === 0 ? args.slice(1)
      : /^(python[\d.]*|py)$/.test(program) && pip > 0 && args[pip - 1] === "-m" ? args.slice(pip + 1)
      : [];
    if (["install", "uninstall"].includes(firstPositional(viaPip))) block(DEPENDENCY);
    // A nested shell's script gets the same check: `bash -lc '…'`, `powershell -Command "…"`.
    if (["bash", "sh", "pwsh", "powershell"].includes(program)) {
      const flag = args.findIndex((a) => /^-([a-z]*c|command)$/i.test(a));
      if (flag >= 0) check(args.slice(flag + 1).join(" "));
    }
  }
}

check(command);
