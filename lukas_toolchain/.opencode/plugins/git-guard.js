// git-guard: håndhæver gruppens git-politik inde i OpenCode, så LLM'en aldrig selv rører git.
//
// last-try: hvert workflow-trin er en OpenCode-kommando, der er bundet direkte til sin rolle-agent
// (ingen orchestrator). Plugin'et hænger derfor på kommandoerne i stedet for task-værktøjet:
// - command.execute.before: tjekker, at OpenCode kører i et demo-repo (egen git-rod, ingen remote,
//   pre-push-hook), committer rester fra en tidligere kommando og vælger branch:
//     code-models, code-storage -> coder_1;  code-api -> coder_2
//     alle andre -> main, efter at coder_1 og coder_2 er merget ind (--no-ff)
//   Begge coder-branches oprettes fra samme commit første gang en coder-kommando køres.
// - event session.idle: committer det, agenten har skrevet, på den aktuelle branch.
// - bash: git-kommandoer blokeres uden for et demo-repo (agenterne har i øvrigt ikke bash).
// - Logger hver kommando som en JSON-linje i .git/git-guard.jsonl (bruges af run_all.sh til summary.md).
import { spawnSync } from "node:child_process"
import { appendFileSync, existsSync } from "node:fs"
import path from "node:path"

const CODER_BRANCH = { "code-models": "coder_1", "code-storage": "coder_1", "code-api": "coder_2" }
const CODERS = ["coder_1", "coder_2"]

export const GitGuard = async ({ directory }) => {
  const running = new Map() // sessionID -> { command, branch, head, t0 }

  const git = (...args) => {
    const res = spawnSync("git", ["-C", directory, "-c", "core.autocrlf=false", ...args], { encoding: "utf8" })
    return { code: res.status ?? 1, out: (res.stdout ?? "").trim(), err: (res.stderr ?? "").trim() }
  }
  const gitOk = (...args) => {
    const res = git(...args)
    if (res.code !== 0) throw new Error(`git-guard: git ${args.join(" ")} fejlede: ${res.err || res.out}`)
    return res.out
  }
  const norm = (p) => {
    const r = path.resolve(p)
    return process.platform === "win32" ? r.toLowerCase() : r
  }
  const demoProblem = () => {
    if (!existsSync(path.join(directory, ".git"))) return `${directory} er ikke et demo-repo (ingen egen .git)`
    const top = git("rev-parse", "--show-toplevel")
    if (top.code !== 0 || norm(top.out) !== norm(directory)) return `${directory} er ikke roden af sit eget git-repo`
    if (git("remote").out) return "demo-repoet har en remote"
    if (!existsSync(path.join(directory, ".git", "hooks", "pre-push"))) return "pre-push-hook mangler"
    return null
  }
  const log = (entry) => {
    try {
      appendFileSync(path.join(directory, ".git", "git-guard.jsonl"), JSON.stringify({ ts: new Date().toISOString(), ...entry }) + "\n")
    } catch {}
  }
  const branch = () => git("branch", "--show-current").out
  const exists = (b) => git("rev-parse", "--verify", "--quiet", `refs/heads/${b}`).code === 0
  const commitLeftovers = (message) => {
    if (!git("status", "--porcelain").out) return false
    gitOk("add", "-A")
    gitOk("commit", "--quiet", "-m", message)
    return true
  }
  const switchTo = (b) => {
    if (branch() === b) return
    gitOk("switch", "--quiet", b)
    log({ event: "switch", branch: b })
  }
  const mergeCoders = () => {
    for (const b of CODERS) {
      if (!exists(b)) continue
      if (git("merge-base", "--is-ancestor", b, "main").code === 0) continue // ingen nye commits
      const res = git("merge", "--no-ff", "--quiet", "-m", `merge: ${b}`, b)
      if (res.code !== 0) {
        git("merge", "--abort")
        log({ event: "merge-conflict", branch: b })
        throw new Error(`git-guard: merge af ${b} ind i main gav konflikt; stoppet`)
      }
      log({ event: "merge", branch: b })
    }
  }

  return {
    "command.execute.before": async (input) => {
      const problem = demoProblem()
      if (problem) throw new Error(`git-guard: ${problem}`)
      // Rester fra en kommando, hvis session.idle ikke nåede at committe dem (fx afbrudt kørsel).
      commitLeftovers("chore(git-guard): rester fra forrige kommando")
      const target = CODER_BRANCH[input.command]
      if (target) {
        if (!CODERS.every(exists)) {
          switchTo("main")
          for (const b of CODERS) if (!exists(b)) gitOk("branch", b)
          log({ event: "branches-created", from: git("rev-parse", "--short", "HEAD").out })
        }
        switchTo(target)
      } else {
        switchTo("main")
        mergeCoders()
      }
      running.set(input.sessionID, { command: input.command, branch: branch(), head: gitOk("rev-parse", "HEAD"), t0: Date.now() })
      log({ event: "command-start", command: input.command, branch: branch() })
    },

    event: async ({ event }) => {
      if (event?.type !== "session.idle") return
      const s = running.get(event.properties?.sessionID)
      if (!s) return
      running.delete(event.properties.sessionID)
      const committed = commitLeftovers(`${s.command}: output fra agenten (committet af git-guard)`)
      const files = git("diff", "--name-only", s.head, "HEAD").out.split("\n").filter(Boolean)
      log({
        event: "command",
        command: s.command,
        branch: s.branch,
        seconds: Math.round((Date.now() - s.t0) / 1000),
        committed,
        files,
      })
    },

    "tool.execute.before": async (input, output) => {
      if (input.tool !== "bash") return
      const cmd = String(output.args?.command ?? "")
      if (/(^|[\s;&|(])git(\s|$)/.test(cmd) && demoProblem()) {
        throw new Error(`git-guard: git er kun tilladt i et demo-repo (${demoProblem()})`)
      }
    },
  }
}
