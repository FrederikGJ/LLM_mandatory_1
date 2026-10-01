"""Starter Aider i demo-repoet med låst git-rod og fast bekræftelsespolitik (planens afsnit 1a).

Kaldes af toolchain.py:
    .venv/bin/python aider_launch.py <demo-dir> <message-fil | -> -- <aider-argumenter>

Med "-" startes Aider interaktivt, så man selv kan godkende architect-planen, se diffs og bruge /undo.
Ellers køres beskeden ikke-interaktivt. Aider svarer så "ja" til de få spørgsmål i ALLOW og "nej" til
resten. Agenten kan dermed ikke oprette filer uden for opgaven, tilføje filer den nævner, køre shell- eller
git-kommandoer eller åbne URL'er.
"""

import os
import sys
from pathlib import Path

from aider.coders.base_coder import Coder
from aider.coders.wholefile_coder import WholeFileCoder
from aider.commands import SwitchCoder
from aider.main import main as aider_main

ALLOW = {
    "Edit the files?",  # architect -> editor
    "Attempt to fix lint errors?",
    "Attempt to fix test errors?",
}


def install_policy(io) -> None:
    def confirm_ask(question, default="y", subject=None, explicit_yes_required=False, **_kwargs):
        answer = question in ALLOW
        target = f" [{subject.splitlines()[0]}]" if subject else ""
        io.tool_output(f"[politik] {question}{target} -> {'ja' if answer else 'nej'}")
        return answer

    io.confirm_ask = confirm_ask


def install_filename_fix() -> None:
    """Små modeller skriver ofte "path/to/components.md" eller "models.py" i stedet for den fulde sti.

    Har filnavnet samme basename som præcis én fil i chatten, bruges den fil. Andre filnavne
    afvises stadig af politikken ("Create new file?" -> nej).
    """
    original = WholeFileCoder.get_edits

    def get_edits(self, mode="update"):
        edits = original(self, mode)
        if mode != "update":
            return edits
        chat_files = self.get_inchat_relative_files()
        fixed, seen = [], set()
        for fname, source, lines in edits:
            if fname not in chat_files:
                matches = [f for f in chat_files if Path(f).name == Path(fname).name]
                if len(matches) == 1:
                    self.io.tool_output(f"[politik] filnavnet {fname} rettes til {matches[0]}")
                    fname = matches[0]
            if fname not in seen:
                seen.add(fname)
                fixed.append((fname, source, lines))
        return fixed

    WholeFileCoder.get_edits = get_edits


def main() -> int:
    if len(sys.argv) < 4 or sys.argv[3] != "--":
        sys.exit(__doc__)
    demo_dir = str(Path(sys.argv[1]).resolve())
    message_file = sys.argv[2]
    argv = sys.argv[4:]

    if message_file == "-":
        return aider_main(argv, force_git_root=demo_dir) or 0

    # Aider giver modellen op til 3 ekstra runder til at rette lint-fejl. På CPU koster hver runde et helt
    # filomskriv med voksende kontekst, så toolchainen sætter loftet (max_reflections i config/roles.yaml).
    Coder.max_reflections = int(os.environ.get("TOOLCHAIN_MAX_REFLECTIONS", Coder.max_reflections))
    coder = aider_main(argv, force_git_root=demo_dir, return_coder=True)
    if not hasattr(coder, "run"):  # Aider stoppede under opstart og returnerede en exit-kode
        return coder or 1
    install_policy(coder.io)
    install_filename_fix()
    coder.show_announcements()
    try:
        coder.run(with_message=Path(message_file).read_text(encoding="utf-8"))
    except SwitchCoder:  # samme håndtering som Aiders egen --message
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
