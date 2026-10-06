// git-guard: håndhæver gruppens git-politik inde i OpenCode, så LLM'en aldrig selv rører branches.
//
// - Før hver task: tjekker, at OpenCode kører i et demo-repo (egen git-rod, ingen remote, pre-push-hook).
//   Ellers blokeres tasken (en fejl i tool.execute.before stopper værktøjet).
// - Før en task til coder_1/coder_2: opretter begge branches fra samme commit (første gang) og skifter til
//   coderens branch.
// - Før en task til enhver anden agent: skifter tilbage til main og merger coder_1 og coder_2 (--no-ff),
//   hvis de har commits, der ikke er i main. Konflikt -> merge --abort og tasken blokeres.
// - Efter hver task: committer det, subagenten har efterladt ucommittet, på den aktuelle branch.
// - Bash: git-kommandoer blokeres helt uden for et demo-repo (fx hvis OpenCode åbnes i llm_mandatory_1).
// - Logger hver task som en JSON-linje i .git/git-guard.jsonl (bruges af run_all.sh til summary.md).
import { spawnSync } from "node:child_process"
import { appendFileSync, existsSync } from "node:fs"
import path from "node:path"

const CODERS = ["coder_1", "coder_2"]

export const GitGuard = async ({ directory }) => {
  const started = new Map() // callID -> { agent, branch, head, t0, session }
  const finished = new Map() // orchestrator-session -> Set af agenter, der er kørt færdig i denne kommando

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
  const commitLeftovers = (who) => {
    if (!git("status", "--porcelain").out) return false
    gitOk("add", "-A")
    gitOk("commit", "--quiet", "-m", `chore(${who}): ucommittede ændringer committet af git-guard`)
    return true
  }
  const switchTo = (b) => {
    if (branch() === b) return
    commitLeftovers("git-guard")
    gitOk("switch", "--quiet", b)
    log({ event: "switch", branch: b })
  }
  const mergeCoders = () => {
    for (const b of CODERS) {
      if (!exists(b)) continue
      if (git("merge-base", "--is-ancestor", b, "main").code === 0) continue // allerede merget / ingen nye commits
      const res = git("merge", "--no-ff", "--quiet", "-m", `merge: ${b}`, b)
      if (res.code !== 0) {
        // Konflikt (typisk i docs/tickets, hvor begge coders retter Status). Nyt forsøg, hvor coderens
        // version vinder i de konfliktende hunks; ikke-konfliktende ændringer fra begge sider bevares.
        git("merge", "--abort")
        const retry = git("merge", "--no-ff", "--quiet", "-X", "theirs", "-m", `merge: ${b} (-X theirs)`, b)
        if (retry.code !== 0) {
          git("merge", "--abort")
          log({ event: "merge-conflict", branch: b })
          throw new Error(`git-guard: merge af ${b} ind i main gav konflikt; stoppet`)
        }
        log({ event: "merge-theirs", branch: b })
        continue
      }
      log({ event: "merge", branch: b })
    }
  }

  return {
    "tool.execute.before": async (input, output) => {
      if (input.tool === "bash") {
        const cmd = String(output.args?.command ?? "")
        if (/(^|[\s;&|(])git(\s|$)/.test(cmd) && demoProblem()) {
          throw new Error(`git-guard: git er kun tilladt i et demo-repo (${demoProblem()})`)
        }
        return
      }
      if (input.tool !== "task") return

      const problem = demoProblem()
      if (problem) throw new Error(`git-guard: ${problem}`)
      const agent = String(output.args?.subagent_type ?? "")

      // Små orchestrator-modeller starter tasks parallelt og gentager trin, der allerede er færdige.
      // Begge dele blokeres her deterministisk; fejlteksten går tilbage til orchestratoren som tool-resultat.
      if ([...started.values()].some((t) => t.session === input.sessionID)) {
        log({ event: "blocked-parallel", agent })
        throw new Error(`git-guard: another task is still running. Wait for its result, then call ${agent} as the next step.`)
      }
      if (finished.get(input.sessionID)?.has(agent)) {
        log({ event: "blocked-repeat", agent })
        throw new Error(`git-guard: ${agent} has already finished in this command. Do not run it again. Run the next step, or if all steps are done, list the changed files and stop.`)
      }

      if (CODERS.includes(agent)) {
        if (!CODERS.every(exists)) {
          switchTo("main")
          for (const b of CODERS) if (!exists(b)) gitOk("branch", b)
          log({ event: "branches-created", from: git("rev-parse", "--short", "HEAD").out })
        }
        switchTo(agent)
      } else {
        switchTo("main")
        mergeCoders()
      }
      started.set(input.callID, { agent, branch: branch(), head: gitOk("rev-parse", "HEAD"), t0: Date.now(), session: input.sessionID })
    },

    "tool.execute.after": async (input) => {
      if (input.tool !== "task") return
      const s = started.get(input.callID)
      if (!s) return
      started.delete(input.callID)
      if (!finished.has(s.session)) finished.set(s.session, new Set())
      finished.get(s.session).add(s.agent)
      const commits = Number(git("rev-list", "--count", `${s.head}..HEAD`).out || 0)
      const leftovers = commitLeftovers(s.agent)
      log({
        event: "task",
        agent: s.agent,
        branch: s.branch,
        seconds: Math.round((Date.now() - s.t0) / 1000),
        agent_commits: commits,
        guard_committed: leftovers,
      })
    },
  }
}
